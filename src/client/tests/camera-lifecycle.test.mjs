import assert from 'node:assert/strict';
import test from 'node:test';
import { keepPendingCameraPermission } from '../src/camera-lifecycle.js';

test('only an unopened preview or setup can retain an explicit native permission request', () => {
  const request = {
    opening: true,
    hasStream: false,
    nativeStatus: { attached: true, state: 'permission' },
  };
  for (const stage of ['preview', 'setup']) {
    assert.equal(keepPendingCameraPermission({ ...request, stage }), true);
    assert.equal(
      keepPendingCameraPermission({
        ...request,
        stage,
        nativeStatus: JSON.stringify(request.nativeStatus),
      }),
      true,
    );
    assert.equal(keepPendingCameraPermission({ ...request, stage, hasStream: true }), false);
    assert.equal(keepPendingCameraPermission({ ...request, stage, opening: false }), false);
  }
  for (const stage of ['active', 'paused', 'home', 'done'])
    assert.equal(keepPendingCameraPermission({ ...request, stage }), false);
});

test('ambiguous bridge states never suppress background cleanup', () => {
  for (const nativeStatus of [
    null,
    '{broken',
    { state: 'permission' },
    { attached: false, state: 'permission' },
    { attached: true, permission: true, state: 'idle' },
    { attached: true, state: 'opening' },
    { attached: true, state: 'streaming' },
    { attached: true, state: 'error' },
  ])
    assert.equal(
      keepPendingCameraPermission({
        stage: 'preview',
        opening: true,
        hasStream: false,
        nativeStatus,
      }),
      false,
    );
});
