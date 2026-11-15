import assert from 'node:assert/strict';
import test from 'node:test';

import { applyPaperCaptureProfile } from '../src/camera-profile.js';

test('companion and grading profiles preserve the full portrait UVC track', async () => {
  const settings = { width: 2448, height: 3264 };
  const track = {
    getSettings: () => ({ ...settings }),
    applyConstraints: async (constraints) => {
      Object.assign(settings, {
        width: constraints.width.ideal,
        height: constraints.height.ideal,
      });
    },
  };
  const source = { kind: 'uvc', stream: { getVideoTracks: () => [track] } };
  for (const high of [false, true, false]) {
    await applyPaperCaptureProfile(source, high);
    assert.deepEqual(track.getSettings(), { width: 2448, height: 3264 });
    assert.equal(source.stream.getVideoTracks()[0], track);
  }
});

test('builtin cameras retain their normal and grading resolution requests', async () => {
  const calls = [];
  const source = {
    kind: 'builtin',
    stream: { getVideoTracks: () => [{ applyConstraints: async (value) => calls.push(value) }] },
  };
  await applyPaperCaptureProfile(source);
  await applyPaperCaptureProfile(source, true);
  assert.deepEqual(calls, [
    { width: { ideal: 1280 }, height: { ideal: 960 }, aspectRatio: { ideal: 4 / 3 } },
    {
      width: { ideal: 1600 },
      height: { ideal: 1200 },
      aspectRatio: { ideal: 4 / 3 },
      resizeMode: { exact: 'none' },
    },
  ]);
});

test('closed cameras are ignored and builtin constraint failures remain observable', async () => {
  await applyPaperCaptureProfile(null);
  await applyPaperCaptureProfile({ kind: 'builtin', stream: { getVideoTracks: () => [] } });
  await assert.rejects(
    applyPaperCaptureProfile({
      kind: 'builtin',
      stream: {
        getVideoTracks: () => [
          {
            applyConstraints: async () => {
              throw new Error('unavailable');
            },
          },
        ],
      },
    }),
    /unavailable/,
  );
});
