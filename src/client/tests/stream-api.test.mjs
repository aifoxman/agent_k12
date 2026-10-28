import assert from 'node:assert/strict';
import test from 'node:test';

test('hint stream keeps drawing beats and sentence events intact across network chunks', async () => {
  const previous = {
    fetch: globalThis.fetch,
    location: globalThis.location,
    localStorage: globalThis.localStorage,
    window: globalThis.window,
    CustomEvent: globalThis.CustomEvent,
  };
  globalThis.location = { search: '?mode=child', hostname: 'localhost' };
  globalThis.localStorage = {
    getItem: (name) => (name === 'child_api' ? 'http://localhost:8000' : ''),
  };
  globalThis.window = { dispatchEvent: () => {} };
  globalThis.CustomEvent = class CustomEvent {
    constructor(name, options) {
      this.type = name;
      this.detail = options.detail;
    }
  };
  const body = new TextEncoder().encode(
    'event: beat\ndata: {"speech":"先圈出条件。","page":[{"kind":"circle","box":[0.2,0.3,0.2,0.1]}],"scratch":[]}\n\n' +
      'event: sentence\ndata: {"text":"先找条件。"}\n\n' +
      'event: done\ndata: {"response":"先找条件。","located":true}\n\n',
  );
  globalThis.fetch = async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (let index = 0; index < body.length; index += 7)
            controller.enqueue(body.slice(index, index + 7));
          controller.close();
        },
      }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    );
  try {
    const { streamApi } = await import('../src/api.js');
    const events = [];
    const result = await streamApi('/v1/hints/stream', new FormData(), (name, data) =>
      events.push([name, data]),
    );
    assert.deepEqual(
      events.map(([name]) => name),
      ['beat', 'sentence', 'done'],
    );
    assert.equal(events[0][1].speech, '先圈出条件。');
    assert.equal(events[1][1].text, '先找条件。');
    assert.equal(result.located, true);
  } finally {
    Object.assign(globalThis, previous);
  }
});
