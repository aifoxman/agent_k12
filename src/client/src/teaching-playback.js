import { logicalTeachingSteps, teachingStepKey } from './teaching-progress.js';

// Reception of the complete lesson is independent of the child's playback pace.
export function teachingPause(ms, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve(false);
    const finish = (value) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      resolve(value);
    };
    const abort = () => finish(false);
    const timer = setTimeout(() => finish(true), ms);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

export function createTeachingPlayback({
  play,
  onState,
  pauseMs = 250,
  stepPauseMs = 400,
  rate = 1,
}) {
  const items = [];
  let cursor = 0;
  let finished = false;
  let cancelled = false;
  let complete = false;
  let phase = 'preparing';
  let groupIndex = 0;
  let group = [];
  let groupRead = false;
  let reviewSelection = null;
  let wake = null;
  let action = null;
  let currentAbort = null;
  let repeatRequested = false;
  let replaying = false;
  let interrupted = false;
  let manualHold = false;
  let pauseRequested = false;
  let skipReading = false;
  const totalGroups = () => logicalTeachingSteps(items).length;
  const publish = (next = phase) => {
    phase = next;
    if (!cancelled)
      onState?.({
        phase,
        stepIndex: groupIndex,
        stepCount: totalGroups(),
        receivedComplete: finished,
        title: group.at(-1)?.step.step_title || items[cursor]?.step_title || '',
        slow: rate < 1,
        interrupted,
        holdReason:
          pauseRequested || interrupted
            ? 'pause'
            : manualHold
              ? 'replay'
              : phase === 'waiting'
                ? 'reply'
                : null,
        hasNext: phase === 'waiting' || cursor < items.length || !finished,
      });
  };
  const playItem = async (item, repeat = false) => {
    let played;
    repeatRequested = false;
    currentAbort = new AbortController();
    interrupted = false;
    publish('playing');
    played = await play(item.step, {
      index: item.index,
      rate,
      repeat,
      signal: currentAbort.signal,
    });
    if (currentAbort.signal.aborted && !repeatRequested) played = false;
    if (!cancelled && !currentAbort.signal.aborted) {
      publish('reading');
      await teachingPause(pauseMs, currentAbort.signal);
    }
    currentAbort = null;
    return played;
  };
  const replayGroup = async () => {
    for (const item of reviewSelection || group) {
      if (cancelled) return;
      interrupted = (await playItem(item, true)) === false;
      if (interrupted) return;
    }
  };
  const waitForAction = () => new Promise((resolve) => (action = resolve));
  const needsReply = () => {
    const step = group.at(-1)?.step;
    return (
      step?.isLastInBeat !== false &&
      (['spoken', 'writing'].includes(step?.reply_mode) ||
        ['wait_for_writing', 'ask_for_reasoning'].includes(step?.teaching_move))
    );
  };
  const hold = async (force = false) => {
    if (!force && !interrupted && !pauseRequested && !manualHold && !needsReply()) {
      if (!skipReading) {
        currentAbort = new AbortController();
        publish('reading');
        await teachingPause(stepPauseMs, currentAbort.signal);
        currentAbort = null;
      }
      skipReading = false;
      if (repeatRequested && !cancelled) {
        repeatRequested = false;
        await replayGroup();
      }
      if (!pauseRequested && !manualHold) return;
    }
    while (!cancelled) {
      publish('waiting');
      const command = await waitForAction();
      action = null;
      if (command === 'cancel') return;
      if (command === 'next' || command === 'complete') {
        reviewSelection = null;
        pauseRequested = false;
        manualHold = false;
        if (interrupted && group.length) {
          await replayGroup();
          if (interrupted) continue;
        }
        interrupted = false;
        return;
      }
      pauseRequested = false;
      await replayGroup();
      manualHold = true;
    }
  };
  const drain = async () => {
    while (!cancelled) {
      if (pauseRequested) await hold(true);
      if (cancelled) break;
      if (cursor === items.length) {
        if (finished) break;
        publish('preparing');
        await new Promise((resolve) => (wake = resolve));
        wake = null;
        continue;
      }
      const previous = group.at(-1);
      if (
        group.length &&
        teachingStepKey(previous.step, previous.index) !== teachingStepKey(items[cursor], cursor)
      ) {
        if (!groupRead) await hold();
        if (cancelled) break;
        group = [];
        groupRead = false;
        groupIndex++;
      }
      const item = { step: items[cursor], index: cursor++ };
      group.push(item);
      groupRead = false;
      let played = await playItem(item);
      interrupted = played === false;
      if (cancelled) break;
      if (repeatRequested) {
        repeatRequested = false;
        await replayGroup();
        played = !interrupted;
      }
      if (
        item.step.step_end !== false ||
        played === false ||
        manualHold ||
        pauseRequested ||
        needsReply()
      ) {
        if (finished && cursor === items.length && !interrupted && !manualHold && !pauseRequested)
          break;
        await hold(played === false || manualHold || pauseRequested);
        groupRead = true;
        if (cancelled) break;
        if (finished && cursor === items.length) break;
        // Keep same-ID clips together for progress and replay, even when an
        // older model marked each clip step_end. The next ID opens a new step.
      }
    }
    if (!cancelled) {
      complete = true;
      publish('complete');
    }
    return !cancelled;
  };
  const done = Promise.resolve().then(drain);
  // A rejected speech call belongs to the requesting component's error handler.
  done.catch(() => {});
  return {
    done,
    enqueue(steps) {
      if (cancelled || finished) return;
      items.push(...steps);
      wake?.();
      publish();
    },
    finish() {
      finished = true;
      wake?.();
      if (
        phase === 'waiting' &&
        cursor === items.length &&
        !interrupted &&
        !manualHold &&
        !pauseRequested
      )
        action?.('complete');
      publish();
    },
    command(command) {
      if (cancelled) return;
      if (command === 'slower') rate = 0.85;
      if (command === 'pause') {
        pauseRequested = true;
        currentAbort?.abort();
        wake?.();
        return;
      }
      if (['slower', 'replay'].includes(command) && ['playing', 'reading'].includes(phase)) {
        manualHold = true;
        repeatRequested = true;
        currentAbort?.abort();
      } else if (command === 'next' && phase === 'reading') {
        skipReading = true;
        currentAbort?.abort();
      } else if (action && (command !== 'next' || phase === 'waiting')) {
        action(command);
        action = null;
      } else if (complete && !replaying && ['replay', 'slower'].includes(command)) {
        replaying = true;
        void replayGroup().then(
          () => {
            replaying = false;
            publish('complete');
          },
          () => {
            replaying = false;
            interrupted = true;
            publish('error');
          },
        );
      }
    },
    review(index) {
      const spoken = items.slice(0, cursor);
      const selected = logicalTeachingSteps(spoken).find(
        (step) => index >= step.start && index <= step.end,
      );
      if (!selected) return;
      reviewSelection = spoken
        .slice(selected.start, selected.end + 1)
        .map((step, offset) => ({ step, index: selected.start + offset }));
      this.command('replay');
    },
    interrupt() {
      repeatRequested = false;
      this.command('pause');
    },
    cancel() {
      cancelled = true;
      currentAbort?.abort();
      action?.('cancel');
      wake?.();
      onState?.(null);
    },
    waiting: () => phase === 'waiting',
  };
}
