import assert from 'node:assert/strict';
import test from 'node:test';
import { cameraRotation, canRotateCamera, waitForCameraDimensions } from '../src/camera-view.js';

test('saved rotation accepts only quarter turns and never guesses portrait', () => {
  assert.equal(cameraRotation(null), 0);
  assert.equal(cameraRotation('90'), 90);
  assert.equal(cameraRotation(450), 90);
  assert.equal(cameraRotation(-90), 270);
  for (const value of ['portrait', 45, Infinity, NaN]) assert.equal(cameraRotation(value), 0);
});

test('USB rotation is unavailable during active homework and on the built-in source', () => {
  const external = { preference: 'auto', kind: 'uvc', hasBridge: true };
  for (const stage of ['home', 'paused', 'done', 'preview', 'setup'])
    assert.equal(canRotateCamera({ ...external, stage }), true);
  for (const stage of ['active', 'unknown'])
    assert.equal(canRotateCamera({ ...external, stage }), false);
  assert.equal(canRotateCamera({ ...external, stage: 'preview', kind: 'builtin' }), false);
  assert.equal(canRotateCamera({ ...external, stage: 'home', preference: 'builtin' }), false);
  assert.equal(canRotateCamera({ ...external, stage: 'home', hasBridge: false }), false);
});

test('capture waits for the real video resize instead of trusting the canvas setting', async () => {
  const video = Object.assign(new EventTarget(), { videoWidth: 1920, videoHeight: 1080 });
  let present;
  video.requestVideoFrameCallback = (callback) => (present = callback);
  let ready = false;
  const waiting = waitForCameraDimensions(video, 1080, 1920).then(() => (ready = true));
  await Promise.resolve();
  assert.equal(ready, false);
  video.dispatchEvent(new Event('resize'));
  await Promise.resolve();
  assert.equal(ready, false);
  video.videoWidth = 1080;
  video.videoHeight = 1920;
  video.dispatchEvent(new Event('resize'));
  await Promise.resolve();
  assert.equal(ready, false);
  present();
  await Promise.resolve();
  assert.equal(ready, false);
  present();
  await waiting;
  assert.equal(ready, true);
});

test('a half turn with identical dimensions still waits for newly presented pixels', async () => {
  const video = Object.assign(new EventTarget(), { videoWidth: 1920, videoHeight: 1080 });
  let present;
  video.requestVideoFrameCallback = (callback) => (present = callback);
  let ready = false;
  const waiting = waitForCameraDimensions(video, 1920, 1080).then(() => (ready = true));
  await Promise.resolve();
  assert.equal(ready, false);
  present();
  await Promise.resolve();
  assert.equal(ready, false);
  present();
  await waiting;
  assert.equal(ready, true);
});

test('a queued old orientation is not counted even after video dimensions change', async () => {
  const video = Object.assign(new EventTarget(), { videoWidth: 1080, videoHeight: 1920 });
  let present;
  video.requestVideoFrameCallback = (callback) => (present = callback);
  let ready = false;
  const waiting = waitForCameraDimensions(video, 1080, 1920).then(() => (ready = true));
  present(0, { width: 1920, height: 1080 });
  present(100, { width: 1080, height: 1920 });
  await Promise.resolve();
  assert.equal(ready, false);
  present(200, { width: 1080, height: 1920 });
  await waiting;
  assert.equal(ready, true);
});

test('closing a camera cancels its pending orientation and stale dimensions time out', async () => {
  const video = Object.assign(new EventTarget(), { videoWidth: 1920, videoHeight: 1080 });
  const controller = new AbortController();
  const waiting = waitForCameraDimensions(video, 1080, 1920, { signal: controller.signal });
  controller.abort();
  await assert.rejects(waiting, { name: 'AbortError' });
  await assert.rejects(waitForCameraDimensions(video, 1080, 1920, { timeout: 1 }), /方向仍在调整/);
});
