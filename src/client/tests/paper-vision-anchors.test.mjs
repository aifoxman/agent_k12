import assert from 'node:assert/strict';
import test from 'node:test';
import { webcrypto } from 'node:crypto';
import { recognizeHintAnchors } from '../src/paper-vision-anchors.js';

const image = new Blob(['same-jpeg-bytes'], { type: 'image/jpeg' });
const valid = {
  id: 'e1',
  level: 'element',
  text: '条件',
  box: [0.1, 0.2, 0.3, 0.1],
  confidence: 0.96,
};
function fixture() {
  const events = new EventTarget();
  const calls = { recognize: [], cancel: [], bitmapClosed: 0 };
  const environment = {
    crypto: webcrypto,
    btoa,
    setTimeout,
    clearTimeout,
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    createImageBitmap: async () => ({
      width: 640,
      height: 480,
      close() {
        calls.bitmapClosed++;
      },
    }),
    PaperVision: {
      recognizeAnchors(base64, id) {
        calls.recognize.push({ base64, id });
      },
      cancelAnchors(id) {
        calls.cancel.push(id);
      },
    },
  };
  return {
    environment,
    calls,
    async reply(change = {}) {
      while (!calls.recognize.length) await new Promise((resolve) => setImmediate(resolve));
      const digest = await webcrypto.subtle.digest('SHA-256', await image.arrayBuffer());
      events.dispatchEvent(
        new CustomEvent('paper-vision-anchors', {
          detail: {
            id: calls.recognize[0].id,
            image_sha256: Buffer.from(digest).toString('hex'),
            width: 640,
            height: 480,
            anchors: [valid],
            ...change,
          },
        }),
      );
    },
  };
}

test('local anchors use exactly the captured bytes, dimensions and real confidence', async () => {
  const f = fixture();
  const pending = recognizeHintAnchors(image, { environment: f.environment });
  await f.reply();
  const result = await pending;
  assert.equal(Buffer.from(f.calls.recognize[0].base64, 'base64').toString(), 'same-jpeg-bytes');
  assert.deepEqual(result.anchors, [valid]);
  assert.equal(result.width, 640);
  assert.equal(result.height, 480);
  assert.equal(f.calls.bitmapClosed, 1);
  assert.equal(f.calls.recognize.length, 1);
});
test('wrong-frame hashes and dimensions never become hint evidence', async () => {
  for (const change of [{ image_sha256: '0'.repeat(64) }, { width: 480 }, { error: 'busy' }]) {
    const f = fixture();
    const pending = recognizeHintAnchors(image, { environment: f.environment });
    await f.reply(change);
    assert.equal(await pending, null);
  }
});
test('unknown or low confidence and invalid boxes are omitted rather than repaired', async () => {
  const f = fixture();
  const pending = recognizeHintAnchors(image, { environment: f.environment });
  await f.reply({
    anchors: [
      valid,
      null,
      false,
      ...[null, 0.89, NaN, 1.1].map((confidence) => ({ ...valid, confidence })),
      { ...valid, box: [0.9, 0.2, 0.3, 0.1] },
      { ...valid, level: 'guessed' },
    ],
  });
  assert.deepEqual((await pending).anchors, [valid]);
});
test('abort and task/run invalidation discard late native results and cancel the request', async () => {
  for (const abort of [true, false]) {
    const f = fixture();
    const controller = new AbortController();
    let current = true;
    const pending = recognizeHintAnchors(image, {
      environment: f.environment,
      signal: controller.signal,
      isCurrent: () => current,
    });
    while (!f.calls.recognize.length) await new Promise((resolve) => setImmediate(resolve));
    if (abort) controller.abort();
    else current = false;
    await f.reply();
    assert.equal(await pending, null);
    assert.equal(f.calls.cancel.length, 1);
  }
});
test('a bounded timeout cancels native work without blocking a server hint', async () => {
  const f = fixture();
  const result = await recognizeHintAnchors(image, { environment: f.environment, timeoutMs: 10 });
  assert.equal(result, null);
  assert.equal(f.calls.cancel.length, 1);
});
test('a bitmap finishing after timeout is released and never starts a late OCR call', async () => {
  const f = fixture();
  let release;
  f.environment.createImageBitmap = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const pending = recognizeHintAnchors(image, { environment: f.environment, timeoutMs: 5 });
  assert.equal(await pending, null);
  release({
    width: 640,
    height: 480,
    close() {
      f.calls.bitmapClosed++;
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls.bitmapClosed, 1);
  assert.equal(f.calls.recognize.length, 0);
});

test('the serialized local anchor payload respects the native 48 KiB UTF-8 budget', async () => {
  const f = fixture();
  const pending = recognizeHintAnchors(image, { environment: f.environment });
  await f.reply({
    anchors: Array.from({ length: 120 }, (_, index) => ({
      ...valid,
      id: `${index}`.padEnd(80, 'x'),
      text: '😀'.repeat(80),
    })),
  });
  assert.equal(await pending, null);
});
