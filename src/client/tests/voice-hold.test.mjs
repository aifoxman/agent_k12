import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.location = { search: '?mode=child', hostname: 'localhost' };
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
};

const { speak, startSpeechSession } = await import('../src/api.js');

test('a hint follow-up stays in the caption without entering the spoken audio', () => {
  const events = [];
  const audio = [];
  globalThis.CustomEvent = class {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  };
  globalThis.window = {
    dispatchEvent: (event) => events.push(event),
    PaperVoice: { speak: (phrase) => audio.push(phrase) },
  };
  speak('先找出题目问什么。', ['继续提示'], '先找出题目问什么。如果还需要下一步，请说继续提示。');
  assert.deepEqual(audio, ['先找出题目问什么。']);
  assert.equal(events[0].detail.text.includes('继续提示'), true);
  assert.equal(events[0].detail.spoken.includes('继续提示'), false);
});

test('a held voice command sends one transcript only after release', () => {
  const calls = [];
  const heard = [];
  globalThis.window = {
    PaperVoice: {
      listenHold: () => calls.push('listenHold'),
      stopListening: () => calls.push('stop'),
      cancelListening: () => calls.push('cancel'),
    },
  };

  const session = startSpeechSession({
    holdToTalk: true,
    onText: (value) => heard.push(value),
    onError: (value) => assert.fail(value),
  });
  window.__nativeSpeechPartial('我想问这道题');
  assert.deepEqual(heard, []);
  session.stop();
  assert.deepEqual(heard, []);
  window.__nativeSpeechResult('我想问这道题怎么做', '', '');
  window.__nativeSpeechResult('重复结果', '', '');
  assert.deepEqual(heard, ['我想问这道题怎么做']);
  assert.deepEqual(calls, ['listenHold', 'stop', 'cancel']);
});

test('system ending recognition early waits for release without opening a new session', async () => {
  const heard = [];
  let listens = 0;
  globalThis.window = {
    PaperVoice: {
      listenHold: () => listens++,
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    holdToTalk: true,
    onText: (value) => heard.push(value),
    onError: (value) => assert.fail(value),
  });
  window.__nativeSpeechPartial('前半句');
  window.__nativeSpeechState('ended');
  assert.deepEqual(heard, []);
  await new Promise((resolve) => setTimeout(resolve, 230));
  assert.equal(listens, 1);
  session.stop();
  window.__nativeSpeechResult('前半句', '', '');
  assert.deepEqual(heard, ['前半句']);
});

test('releasing without a recognized phrase gives a retry prompt', () => {
  const errors = [];
  globalThis.window = {
    PaperVoice: {
      listenHold: () => {},
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    holdToTalk: true,
    onText: () => assert.fail('empty speech must not send a command'),
    onError: (value) => errors.push(value),
  });
  session.stop();
  window.__nativeSpeechResult('', '', 'empty');
  assert.deepEqual(errors, ['没有听清，请按住说话重试']);
});

test('a useful interim transcript survives an empty final result after release', () => {
  const heard = [];
  globalThis.window = {
    PaperVoice: {
      listenHold: () => {},
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    holdToTalk: true,
    onText: (value) => heard.push(value),
    onError: (value) => assert.fail(value),
  });
  window.__nativeSpeechPartial('第三大题的第三小题');
  session.stop();
  window.__nativeSpeechResult('', '', 'empty');
  assert.deepEqual(heard, ['第三大题的第三小题']);
});

test('an actual recognizer error does not turn an interim transcript into a command', () => {
  const errors = [];
  globalThis.window = {
    PaperVoice: {
      listenHold: () => {},
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    holdToTalk: true,
    onText: () => assert.fail('recognizer error must not send a command'),
    onError: (value) => errors.push(value),
  });
  window.__nativeSpeechPartial('第三大题');
  session.stop();
  window.__nativeSpeechResult('', '系统语音识别服务暂不可用', '');
  assert.deepEqual(errors, ['系统语音识别服务暂不可用']);
});

test('a provisional confirmation is never submitted as a final command', () => {
  const errors = [];
  globalThis.window = {
    PaperVoice: {
      listenHold: () => {},
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    holdToTalk: true,
    onText: () => assert.fail('partial confirmation must not be submitted'),
    onError: (value) => errors.push(value),
  });
  window.__nativeSpeechPartial('确认结束作业');
  session.stop();
  window.__nativeSpeechResult('', '', 'empty');
  assert.equal(errors.length, 1);
});

test('a silent recognizer result ends the reply without forced reconnection', async () => {
  let listens = 0;
  const heard = [];
  const finished = [];
  globalThis.window = {
    PaperVoice: {
      listen: () => listens++,
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  startSpeechSession({
    retrySilence: true,
    onText: (value) => heard.push(value),
    onError: (value) => assert.fail(value),
    onFinish: (result) => finished.push(result),
  });
  window.__nativeSpeechResult('', '', 'empty');
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(listens, 1);
  window.__nativeSpeechResult('第三大题第三小题', '', '');
  assert.deepEqual(heard, []);
  assert.equal(finished.length, 1);
  assert.equal(finished[0].text, '');
});

test('an automatic reply submits the system final once and never reopens the microphone', async () => {
  const heard = [];
  const partials = [];
  let listens = 0;
  globalThis.window = {
    PaperVoice: {
      listenReply: () => listens++,
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    collectUntilPause: true,
    onText: (value) => heard.push(value),
    onPartial: (value) => partials.push(value),
    onError: (value) => assert.fail(value),
  });
  assert.equal(listens, 1);
  window.__nativeSpeechPartial('第三大题');
  assert.deepEqual(heard, []);
  window.__nativeSpeechPartial('第三大题的第三小题');
  assert.deepEqual(partials, ['第三大题', '第三大题的第三小题']);
  window.__nativeSpeechResult('第三大题的第三小题', '', '');
  assert.deepEqual(heard, ['第三大题的第三小题']);
  await new Promise((resolve) => setTimeout(resolve, 230));
  assert.equal(listens, 1);
  session.stop();
});

test('an automatic reply does not submit an unconfirmed interim authorization', () => {
  const heard = [];
  globalThis.window = {
    PaperVoice: {
      listenReply: () => {},
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  const session = startSpeechSession({
    collectUntilPause: true,
    onText: (value) => heard.push(value),
    onError: () => {},
  });
  window.__nativeSpeechPartial('确认允许批改');
  session.stop();
  window.__nativeSpeechResult('', '', 'empty');
  assert.deepEqual(heard, []);
});

test('a recognizer failure after one segment keeps the words for retry without submitting them', () => {
  const heard = [];
  const finished = [];
  globalThis.window = {
    PaperVoice: {
      listenReply: () => {},
      stopListening: () => {},
      cancelListening: () => {},
    },
  };
  startSpeechSession({
    collectUntilPause: true,
    onText: (value) => heard.push(value),
    onError: () => {},
    onFinish: (result) => finished.push(result),
  });
  window.__nativeSpeechPartial('我在看题');
  window.__nativeSpeechResult('我在看题', '系统语音识别服务暂不可用', '');
  assert.deepEqual(heard, []);
  assert.equal(finished[0].partial, '我在看题');
  assert.equal(finished[0].text, '');
  assert.equal(finished[0].error, '系统语音识别服务暂不可用');
});
