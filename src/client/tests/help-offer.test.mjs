import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createHelpOfferDeadline,
  HELP_REPLY_WAIT_MS,
  helpOfferCanStart,
} from '../src/help-offer.js';
import { parseVoiceReply } from '../src/voice-intents.js';

function clock() {
  let time = 1;
  let id = 0;
  const tasks = new Map();
  const calls = [];
  const deadline = createHelpOfferDeadline({
    now: () => time,
    onTimeout: (context) => calls.push(context),
    setTimer: (callback, delay) => {
      tasks.set(++id, { callback, at: time + delay });
      return id;
    },
    clearTimer: (key) => tasks.delete(key),
  });
  const advance = (ms) => {
    time += ms;
    for (const [key, task] of [...tasks])
      if (task.at <= time) {
        tasks.delete(key);
        task.callback();
      }
  };
  return { deadline, calls, advance, tasks };
}

test('silence continues one help offer after twenty seconds, including a microphone unavailable turn', () => {
  const c = clock();
  c.deadline.arm('help_offer');
  c.advance(HELP_REPLY_WAIT_MS - 1);
  assert.deepEqual(c.calls, []);
  c.advance(1);
  assert.deepEqual(c.calls, ['help_offer']);
  c.advance(HELP_REPLY_WAIT_MS);
  assert.equal(c.calls.length, 1);
});

test('a refusal, pause, new question or app background cancels the pending automatic lesson', () => {
  const c = clock();
  for (const words of ['不要', '让我自己做', '我先自己做']) {
    assert.equal(parseVoiceReply('help_offer', words).type, 'decline');
    c.deadline.arm('help_offer');
    c.deadline.cancel();
    c.advance(HELP_REPLY_WAIT_MS);
  }
  assert.deepEqual(c.calls, []);
  c.deadline.arm('setup_camera');
  c.advance(HELP_REPLY_WAIT_MS);
  assert.deepEqual(c.calls, []);
});

test('recorded ASR must finish before silence can continue; a spoken no cannot race the deadline', () => {
  const c = clock();
  c.deadline.arm('help_offer');
  c.advance(19_000);
  c.deadline.hold();
  c.advance(10_000);
  assert.deepEqual(c.calls, []);
  // A real transcript cancels. An empty transcript resumes an already elapsed wait.
  c.deadline.cancel();
  c.advance(1);
  assert.deepEqual(c.calls, []);
  c.deadline.arm('help_offer');
  c.deadline.hold();
  c.advance(30_000);
  c.deadline.resume('help_offer');
  c.advance(0);
  assert.deepEqual(c.calls, ['help_offer']);
});

test('replacement and late recognizer callbacks cannot fire a cancelled offer', () => {
  const c = clock();
  c.deadline.arm('help_offer');
  const stale = [...c.tasks.values()][0].callback;
  c.deadline.cancel();
  c.deadline.arm('help_offer');
  stale();
  assert.deepEqual(c.calls, []);
  c.advance(HELP_REPLY_WAIT_MS);
  assert.deepEqual(c.calls, ['help_offer']);
});

test('a timeout may start only the current active session, camera orientation and available turn', () => {
  const offer = { id: 'candidate', sessionId: 'session', frameRevision: 4 };
  const scope = { sessionId: 'session', frameRevision: 4, stage: 'active', proactive: true };
  assert.equal(helpOfferCanStart(offer, scope), true);
  for (const change of [
    { sessionId: 'other' },
    { frameRevision: 5 },
    { stage: 'paused' },
    { proactive: false },
    { busy: true },
    { hidden: true },
  ])
    assert.equal(helpOfferCanStart(offer, { ...scope, ...change }), false);
  assert.equal(helpOfferCanStart(null, scope), false);
});
