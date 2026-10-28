import { useEffect, useRef, useState } from 'react';
import { key, post } from './api.js';

const blank = () => ({
  learning_seconds: 0,
  focus_seconds: 0,
  assisted_seconds: 0,
  grading_wait_seconds: 0,
  paused_seconds: 0,
  by_subject: [],
  last_seq: 0,
});

function purgeOldTimerStorage() {
  const prefix = key('timer_');
  for (let index = localStorage.length - 1; index >= 0; index--) {
    const name = localStorage.key(index);
    if (!name?.startsWith(prefix)) continue;
    try {
      const savedAt = JSON.parse(localStorage.getItem(name) || '{}').saved_at;
      if (!savedAt || savedAt < Date.now() - 7 * 86400000) localStorage.removeItem(name);
    } catch {
      localStorage.removeItem(name);
    }
  }
}

export function formatStudyTime(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function spokenMinutes(value) {
  if (/^\d{1,3}$/.test(value)) return Number(value);
  const digit = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (!/^[零一二两三四五六七八九十百]+$/.test(value)) return NaN;
  const [hundreds, rest = ''] = value.split('百');
  const head = value.includes('百') ? (digit[hundreds] || 1) * 100 : 0;
  const part = value.includes('百') ? rest.replace(/^零/, '') : value;
  if (!part) return head;
  const [tens, ones = ''] = part.split('十');
  return (
    head +
    (part.includes('十') ? (tens ? digit[tens] : 1) * 10 + (ones ? digit[ones] : 0) : digit[part])
  );
}

export function parseTimerCommand(text) {
  const value = String(text || '').replace(/[，。！？,.!?\s]/g, '');
  if (/^(做了多久|学了多久|用了多久|还剩多久|剩多久)$/.test(value)) return { type: 'status' };
  const target = value.match(
    /^(?:目标)?(?:改成|设为|设置为)([\d零一二两三四五六七八九十百]{1,5})分钟$/,
  );
  const minutes = target ? spokenMinutes(target[1]) : NaN;
  if (minutes >= 5 && minutes <= 180) return { type: 'goal', minutes };
  if (/^(不设目标|取消目标)$/.test(value)) return { type: 'goal', minutes: null };
  const subject = value.match(/^(?:现在做|切换到|换成)([\p{L}\p{N}·-]{1,30}?)(?:作业)?$/u);
  if (subject) return { type: 'subject', subject: subject[1] };
  return null;
}

export class TimerLedger {
  constructor(sessionId, serverTimer, serverNow, receivedMonoMs, changed = () => {}) {
    this.sessionId = sessionId;
    this.storageKey = key(`timer_${sessionId}`);
    let stored = {};
    try {
      stored = JSON.parse(localStorage.getItem(this.storageKey) || '{}');
    } catch {
      stored = {};
    }
    if (stored.saved_at && stored.saved_at < Date.now() - 7 * 86400000) {
      localStorage.removeItem(this.storageKey);
      stored = {};
    }
    this.server = serverTimer || blank();
    this.queue = Array.isArray(stored.queue)
      ? stored.queue.filter((item) => item.seq > (this.server.last_seq || 0))
      : [];
    this.seq = Math.max(
      this.server.last_seq || 0,
      stored.seq || 0,
      ...this.queue.map((item) => item.seq),
    );
    this.changed = changed;
    this.activeAt = null;
    this.kind = 'focus';
    this.subject = null;
    this.syncing = null;
    this.syncFailed = false;
    this.monoAnchor = performance.now();
    this.serverAnchorMs =
      (serverNow || Date.now() / 1000) * 1000 +
      Math.max(0, this.monoAnchor - (receivedMonoMs ?? this.monoAnchor));
  }

  persist() {
    if (this.destroyed) return;
    localStorage.setItem(
      this.storageKey,
      JSON.stringify({
        queue: this.queue,
        seq: this.seq,
        was_active: this.activeAt !== null,
        saved_at: Date.now(),
      }),
    );
    this.changed();
  }

  updateServer(snapshot) {
    if (this.destroyed) return;
    if (!snapshot || (snapshot.last_seq || 0) < (this.server.last_seq || 0)) return;
    this.server = snapshot;
    this.queue = this.queue.filter((item) => item.seq > snapshot.last_seq);
    this.seq = Math.max(this.seq, snapshot.last_seq || 0);
    this.persist();
  }

  checkpoint() {
    if (this.activeAt === null) return;
    const now = performance.now();
    let remaining = Math.max(0, Math.round(now - this.activeAt));
    let consumed = 0;
    while (remaining > 0) {
      const duration = Math.min(10000, remaining);
      remaining -= duration;
      consumed += duration;
      this.seq += 1;
      this.queue.push({
        id: `${this.sessionId}:${this.seq}`,
        seq: this.seq,
        kind: this.kind,
        subject: this.subject,
        duration_ms: duration,
        ended_at: Math.floor((this.serverAnchorMs + now - this.monoAnchor - remaining) / 1000),
      });
    }
    this.activeAt = now;
    if (consumed) this.persist();
  }

  setMode(active, kind = 'focus', subject = null) {
    if (this.activeAt !== null && (this.kind !== kind || this.subject !== subject || !active))
      this.checkpoint();
    this.kind = kind;
    this.subject = subject;
    if (active && this.activeAt === null) this.activeAt = performance.now();
    if (!active) this.activeAt = null;
    this.persist();
  }

  async flush() {
    if (this.destroyed) return;
    if (this.syncing) {
      await this.syncing;
      if (this.queue.length && !this.syncFailed) return this.flush();
      return;
    }
    if (!this.queue.length) return;
    const work = (async () => {
      while (this.queue.length && !this.destroyed) {
        try {
          const batch = this.queue.slice(0, 120);
          const result = await post(`/v1/sessions/${this.sessionId}/timer/events`, {
            events: batch,
          });
          this.syncFailed = false;
          this.updateServer(result);
          if ((result.last_seq || 0) < batch.at(-1).seq)
            throw new Error('Timer acknowledgement made no progress');
        } catch {
          this.syncFailed = true;
          this.changed();
          break;
        }
      }
    })();
    this.syncing = work;
    try {
      await work;
    } finally {
      if (this.syncing === work) this.syncing = null;
    }
  }

  clear() {
    this.destroyed = true;
    this.activeAt = null;
    this.queue = [];
    localStorage.removeItem(this.storageKey);
    this.changed();
  }

  totals() {
    const totals = {
      focus: (this.server.focus_seconds || 0) * 1000,
      assisted: (this.server.assisted_seconds || 0) * 1000,
      grading_wait: (this.server.grading_wait_seconds || 0) * 1000,
    };
    const subjects = new Map(
      (this.server.by_subject || []).map((item) => [item.subject || '', item.seconds * 1000]),
    );
    const add = (kind, subject, duration) => {
      totals[kind] += duration;
      if (kind !== 'grading_wait')
        subjects.set(subject || '', (subjects.get(subject || '') || 0) + duration);
    };
    for (const item of this.queue) add(item.kind, item.subject, item.duration_ms);
    if (this.activeAt !== null)
      add(this.kind, this.subject, Math.max(0, performance.now() - this.activeAt));
    const focusSeconds = Math.floor(totals.focus / 1000);
    const assistedSeconds = Math.floor(totals.assisted / 1000);
    return {
      learning_seconds: focusSeconds + assistedSeconds,
      focus_seconds: focusSeconds,
      assisted_seconds: assistedSeconds,
      grading_wait_seconds: Math.floor(totals.grading_wait / 1000),
      paused_seconds: this.server.paused_seconds || 0,
      by_subject: [...subjects].map(([subject, ms]) => ({
        subject: subject || null,
        seconds: Math.floor(ms / 1000),
      })),
      pending: this.queue.length > 0 && this.syncFailed,
    };
  }
}

export function useSessionTimer({ sessionId, active, kind, subject, dashboard }) {
  const ledgerRef = useRef(null);
  const [, setVersion] = useState(0);
  useEffect(purgeOldTimerStorage, []);
  if (sessionId && ledgerRef.current?.sessionId !== sessionId) {
    ledgerRef.current = new TimerLedger(
      sessionId,
      dashboard.timer,
      dashboard.server_now,
      dashboard.received_mono_ms,
      () => setVersion((value) => value + 1),
    );
  }
  if (!sessionId) ledgerRef.current = null;
  useEffect(() => {
    ledgerRef.current?.updateServer(dashboard.timer);
  }, [dashboard.timer]);
  useEffect(() => {
    ledgerRef.current?.setMode(active, kind, subject);
  }, [sessionId, active, kind, subject]);
  useEffect(() => {
    if (!sessionId) return;
    let ticks = 0;
    const interval = setInterval(() => {
      const ledger = ledgerRef.current;
      ticks += 1;
      if (ticks % 5 === 0) ledger?.checkpoint();
      if (active) setVersion((value) => value + 1);
      if (ticks % 60 === 0) ledger?.flush();
    }, 1000);
    return () => clearInterval(interval);
  }, [sessionId, active]);
  const ledger = ledgerRef.current;
  return {
    ...(!sessionId || !ledger ? blank() : ledger.totals()),
    stop: async () => {
      ledger?.setMode(false);
      await ledger?.flush();
    },
    flush: () => ledger?.flush(),
    clear: () => ledger?.clear(),
  };
}
