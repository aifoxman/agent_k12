import assert from 'node:assert/strict';
import test from 'node:test';

const values = new Map([['child_api', 'http://localhost:8000']]);
globalThis.location = { search: '?mode=child', hostname: 'localhost' };
globalThis.localStorage = {
  getItem: (name) => values.get(name) ?? null,
  setItem: (name, value) => values.set(name, value),
  removeItem: (name) => values.delete(name),
  key: (index) => [...values.keys()][index] ?? null,
  get length() {
    return values.size;
  },
};

const { TimerLedger, formatStudyTime, parseTimerCommand } = await import('../src/session-timer.js');

test('voice timer commands accept spoken numbers and arbitrary subject labels', () => {
  assert.deepEqual(parseTimerCommand('目标改成三十五分钟'), { type: 'goal', minutes: 35 });
  assert.deepEqual(parseTimerCommand('切换到地理作业'), { type: 'subject', subject: '地理' });
  assert.deepEqual(parseTimerCommand('还剩多久'), { type: 'status' });
  assert.equal(formatStudyTime(125), '2:05');
});

test('unsent foreground time survives restart and is removed after acknowledgement', async () => {
  const previousFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => {
      throw new TypeError('offline');
    };
    const first = new TimerLedger('session-a', null, 1000, performance.now());
    first.setMode(true, 'focus', '地理');
    first.activeAt -= 5000;
    first.checkpoint();
    first.setMode(false);
    await first.flush();
    assert.equal(first.queue.length, 1);
    assert.equal(first.syncFailed, true);

    globalThis.fetch = async (_url, options) => {
      const events = JSON.parse(options.body).events;
      assert.equal(events.length, 1);
      assert.equal(events[0].subject, '地理');
      return {
        ok: true,
        json: async () => ({
          last_seq: events[0].seq,
          focus_seconds: 5,
          assisted_seconds: 0,
          grading_wait_seconds: 0,
          by_subject: [{ subject: '地理', seconds: 5 }],
        }),
      };
    };
    const restored = new TimerLedger('session-a', null, 1000, performance.now());
    assert.equal(restored.queue.length, 1);
    await restored.flush();
    assert.equal(restored.queue.length, 0);
    assert.equal(restored.totals().learning_seconds, 5);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('stopping while a timer upload is running waits for the remaining time', async () => {
  const previousFetch = globalThis.fetch;
  let releaseFirst;
  const firstReply = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  let requests = 0;
  const sent = [];
  try {
    globalThis.fetch = async (_url, options) => {
      const events = JSON.parse(options.body).events;
      requests++;
      sent.push(events.map((item) => item.seq));
      if (requests === 1) await firstReply;
      return {
        ok: true,
        json: async () => ({
          last_seq: events.at(-1).seq,
          focus_seconds: events.at(-1).seq * 5,
          assisted_seconds: 0,
          grading_wait_seconds: 0,
          by_subject: [{ subject: '语文', seconds: events.at(-1).seq * 5 }],
        }),
      };
    };
    const ledger = new TimerLedger('session-b', null, 1000, performance.now());
    ledger.setMode(true, 'focus', '语文');
    ledger.activeAt -= 5000;
    ledger.checkpoint();
    const uploading = ledger.flush();
    ledger.activeAt -= 5000;
    ledger.setMode(false);
    assert.equal(ledger.queue.length, 2);
    const stopping = ledger.flush();
    releaseFirst();
    await Promise.all([uploading, stopping]);
    assert.deepEqual(sent, [[1], [2]]);
    assert.equal(ledger.queue.length, 0);
    assert.equal(ledger.totals().learning_seconds, 10);
  } finally {
    globalThis.fetch = previousFetch;
    values.delete('child_timer_session-b');
  }
});
