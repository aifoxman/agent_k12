import assert from 'node:assert/strict';
import test from 'node:test';

test('a disconnected API shows a useful error for data and screenshots', async () => {
  const previous = {
    fetch: globalThis.fetch,
    location: globalThis.location,
    localStorage: globalThis.localStorage,
  };
  globalThis.location = { search: '?mode=child', hostname: 'appassets.androidplatform.net' };
  globalThis.localStorage = {
    getItem: (key) => (key === 'child_api' ? 'http://127.0.0.1:8002' : ''),
  };
  globalThis.fetch = async () => {
    throw new TypeError('Failed to fetch');
  };
  try {
    const { api, privateImage, CONNECTION_ERROR } = await import('../src/api.js');
    await assert.rejects(api('/health'), { message: CONNECTION_ERROR });
    await assert.rejects(privateImage('/v1/corrections/image'), {
      message: CONNECTION_ERROR,
    });
  } finally {
    globalThis.fetch = previous.fetch;
    globalThis.location = previous.location;
    globalThis.localStorage = previous.localStorage;
  }
});
