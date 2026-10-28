import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.location = { search: '?mode=child', hostname: 'localhost' };
globalThis.localStorage = {
  getItem: (name) =>
    name === 'child_token' ? 'test-token' : name === 'child_api' ? 'http://localhost:8000' : null,
  setItem: () => {},
};
globalThis.CustomEvent = class {
  constructor(type, options) {
    this.type = type;
    this.detail = options.detail;
  }
};
globalThis.window = { dispatchEvent: () => {} };

const { startRecordedSpeechSession } = await import('../src/api.js');

function installRecorder() {
  const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => tracks }) } },
  });
  globalThis.MediaRecorder = class {
    static isTypeSupported(type) { return type === 'audio/webm;codecs=opus'; }
    constructor(_stream, options) { this.mimeType = options.mimeType; this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      this.ondataavailable({ data: new Blob(['recorded speech']) });
      this.onstop();
    }
  };
  window.MediaRecorder = globalThis.MediaRecorder;
  return tracks;
}

test('recorded speech uploads only after release and returns the full transcript', async () => {
  const tracks = installRecorder();
  const heard = [];
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ text: '我在看题 我想一下' }) };
  };
  const session = startRecordedSpeechSession({
    onText: (value) => heard.push(value),
    onError: (value) => assert.fail(value),
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 0);
  session.stop();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(heard, ['我在看题 我想一下']);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'http://localhost:8000/v1/voice/transcribe');
  assert.equal(requests[0].options.body.get('audio').type, 'audio/webm;codecs=opus');
  assert.equal(tracks[0].stopped, true);
});

test('cancel aborts an in-flight upload without dispatching a transcript', async () => {
  const tracks = installRecorder();
  const heard = [];
  let uploadStarted;
  const started = new Promise((resolve) => { uploadStarted = resolve; });
  globalThis.fetch = (_url, options) => {
    uploadStarted();
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('aborted')));
    });
  };
  const session = startRecordedSpeechSession({
    onText: (value) => heard.push(value),
    onError: (value) => heard.push(value),
  });
  await new Promise((resolve) => setImmediate(resolve));
  session.stop();
  await started;
  session.cancel();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(heard, []);
  assert.equal(tracks[0].stopped, true);
});
