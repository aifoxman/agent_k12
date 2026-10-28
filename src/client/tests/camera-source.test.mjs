import assert from 'node:assert/strict';
import test from 'node:test';

import { openCameraSource } from '../src/camera-source.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function fakeTrack(settings = { width: 1280, height: 960 }) {
  return {
    kind: 'video',
    readyState: 'live',
    stopped: 0,
    requestedFrames: 0,
    getSettings: () => ({ ...settings }),
    requestFrame() {
      this.requestedFrames++;
    },
    stop() {
      this.stopped++;
      this.readyState = 'ended';
    },
  };
}

function fakeStream(track) {
  return {
    getTracks: () => [track],
    getVideoTracks: () => [track],
    getAudioTracks: () => [],
  };
}

function cameraEnvironment({
  native = true,
  openingState = 'streaming',
  width = 1280,
  height = 720,
  framePixels = null,
} = {}) {
  const calls = {
    open: 0,
    close: 0,
    builtin: [],
    fetch: [],
    decode: [],
    draw: [],
    capture: [],
    transforms: [],
  };
  let now = 100_000;
  let timerId = 0;
  const timers = new Map();
  const status = {
    supported: true,
    attached: true,
    permission: true,
    state: 'idle',
    width,
    height,
    frame_id: 1,
    frame_age_ms: 0,
    frame_url: '/native-camera/frame.jpg',
    device_name: 'Synthetic UVC contract fixture',
    generation: '7',
    error: '',
  };
  const builtinTrack = fakeTrack();
  const builtinStream = fakeStream(builtinTrack);
  const canvasTrack = fakeTrack();
  const canvasStream = fakeStream(canvasTrack);
  let currentPixels = framePixels;
  let transform = [1, 0, 0, 1, 0, 0];
  const context = {
    setTransform: (...matrix) => {
      calls.transforms.push(matrix);
      transform = matrix;
    },
    drawImage: (...args) => {
      calls.draw.push(args);
      const bitmap = args[0];
      assert.equal(bitmap.closed, 0, 'a released bitmap must never be painted');
      if (!bitmap.pixels) return;
      const [a, b, c, d, e, f] = transform;
      canvas.pixels = Array.from({ length: canvas.height }, () => Array(canvas.width).fill(null));
      for (let y = 0; y < bitmap.height; y++) {
        for (let x = 0; x < bitmap.width; x++) {
          const outputX = Math.floor(a * (x + 0.5) + c * (y + 0.5) + e);
          const outputY = Math.floor(b * (x + 0.5) + d * (y + 0.5) + f);
          assert.ok(outputX >= 0 && outputX < canvas.width, 'rotated pixel escaped canvas');
          assert.ok(outputY >= 0 && outputY < canvas.height, 'rotated pixel escaped canvas');
          canvas.pixels[outputY][outputX] = bitmap.pixels[y][x];
        }
      }
    },
    clearRect: () => {},
  };
  const canvas = {
    width: 300,
    height: 150,
    getContext: () => context,
    captureStream: (fps) => {
      calls.capture.push(fps);
      return canvasStream;
    },
  };
  canvasTrack.getSettings = () => ({ width: canvas.width, height: canvas.height });
  const response = () => ({
    ok: true,
    status: 200,
    headers: {
      get: (name) => (/generation/i.test(name) ? String(status.generation) : null),
    },
    blob: async () => new Blob(['synthetic-jpeg'], { type: 'image/jpeg' }),
  });
  const environment = {
    location: { origin: 'http://localhost:8003', href: 'http://localhost:8003/app/' },
    Date: { now: () => now },
    performance: { now: () => now },
    setTimeout: (callback, delay = 0) => {
      const id = ++timerId;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    navigator: {
      mediaDevices: {
        getUserMedia: async (constraints) => {
          calls.builtin.push(constraints);
          return builtinStream;
        },
      },
    },
    document: {
      createElement: (name) => {
        assert.equal(name, 'canvas');
        return canvas;
      },
    },
    fetch: async (url, options) => {
      calls.fetch.push({ url: String(url), options });
      return response();
    },
    createImageBitmap: async (blob) => {
      const bitmap = {
        width: status.width,
        height: status.height,
        pixels: currentPixels?.map((row) => [...row]),
        closed: 0,
        close() {
          this.closed++;
        },
      };
      calls.decode.push({ blob, bitmap });
      return bitmap;
    },
  };
  if (native) {
    environment.PaperCamera = {
      status: () => JSON.stringify(status),
      open: () => {
        calls.open++;
        status.state = openingState;
        return JSON.stringify(status);
      },
      close: () => {
        calls.close++;
        status.state = 'idle';
        status.frame_id = 0;
        return JSON.stringify(status);
      },
    };
  }
  return {
    environment,
    status,
    calls,
    canvas,
    canvasTrack,
    canvasStream,
    builtinTrack,
    builtinStream,
    response,
    timers,
    setPixels: (pixels) => {
      currentPixels = pixels;
    },
    advance: (milliseconds) => {
      now += milliseconds;
    },
    runNextTimer: () => {
      const entry = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!entry) return false;
      timers.delete(entry[0]);
      now = Math.max(now, entry[1].at);
      entry[1].callback();
      return true;
    },
  };
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

async function until(fixture, predicate, message) {
  for (let index = 0; index < 80; index++) {
    await flush();
    if (predicate()) return;
    fixture.runNextTimer();
  }
  assert.fail(message);
}

test('ordinary browsers retain a video-only builtin camera and an idempotent stop', async () => {
  const fixture = cameraEnvironment({ native: false });
  const source = await openCameraSource({ environment: fixture.environment });
  assert.equal(source.kind, 'builtin');
  assert.equal(source.stream, fixture.builtinStream);
  assert.equal(fixture.calls.builtin.length, 1);
  assert.equal(fixture.calls.builtin[0].audio, false);
  assert.ok(fixture.calls.builtin[0].video);
  assert.equal(source.stream.getAudioTracks().length, 0);
  source.assertFresh();
  source.stop();
  source.stop();
  assert.equal(fixture.builtinTrack.stopped, 1);
  assert.throws(() => source.assertFresh());
});

test('an explicit builtin preference does not open an attached USB camera', async () => {
  const fixture = cameraEnvironment();
  const source = await openCameraSource({
    preference: 'builtin',
    environment: fixture.environment,
  });
  assert.equal(source.kind, 'builtin');
  assert.equal(fixture.calls.open, 0);
  assert.equal(fixture.calls.fetch.length, 0);
  source.stop();
});

test('UVC publishes a single video track only after decoding its first real-size JPEG', async () => {
  const fixture = cameraEnvironment();
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  let settled = false;
  const pending = openCameraSource({ environment: fixture.environment }).then((source) => {
    settled = true;
    return source;
  });
  await until(fixture, () => fixture.calls.decode.length === 1, 'first JPEG was not decoded');
  assert.equal(settled, false);
  assert.equal(fixture.calls.capture.length, 0);
  decoded.resolve();
  const source = await pending;
  assert.equal(source.kind, 'uvc');
  assert.equal(source.stream, fixture.canvasStream);
  assert.equal(source.stream.getVideoTracks().length, 1);
  assert.equal(source.stream.getAudioTracks().length, 0);
  assert.equal(fixture.canvas.width, 1280);
  assert.equal(fixture.canvas.height, 720);
  assert.equal(fixture.calls.draw.length, 1);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 0);
  assert.equal(fixture.calls.builtin.length, 0);
  assert.match(fixture.calls.fetch[0].url, /\/native-camera\/frame\.jpg(?:\?|$)/);
  assert.equal(fixture.calls.fetch[0].options.cache, 'no-store');
  source.assertFresh();
  source.stop();
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
});

test('aborting builtin acquisition stops a late media result instead of publishing it', async () => {
  const fixture = cameraEnvironment({ native: false });
  const media = deferred();
  fixture.environment.navigator.mediaDevices.getUserMedia = async (constraints) => {
    fixture.calls.builtin.push(constraints);
    return media.promise;
  };
  const controller = new AbortController();
  const pending = openCameraSource({ signal: controller.signal, environment: fixture.environment });
  const rejected = assert.rejects(pending, (error) => error.name === 'AbortError');
  await until(
    fixture,
    () => fixture.calls.builtin.length === 1,
    'builtin acquisition did not start',
  );
  controller.abort();
  media.resolve(fixture.builtinStream);
  await rejected;
  await flush();
  assert.equal(fixture.builtinTrack.stopped, 1);
});

test('aborting UVC first-frame decode closes native capture and discards a late bitmap', async () => {
  const fixture = cameraEnvironment();
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  const unavailable = [];
  const controller = new AbortController();
  const pending = openCameraSource({
    signal: controller.signal,
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  const rejected = assert.rejects(pending, (error) => error.name === 'AbortError');
  await until(fixture, () => fixture.calls.decode.length === 1, 'first-frame decode did not start');
  controller.abort();
  await rejected;
  decoded.resolve();
  await flush();
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.calls.draw.length, 0);
  assert.equal(fixture.calls.capture.length, 0);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(fixture.calls.builtin.length, 0);
  assert.equal(unavailable.length, 0);
});

test('a UVC permission failure is surfaced without silently using a builtin lens', async () => {
  const fixture = cameraEnvironment({ openingState: 'error' });
  fixture.status.permission = false;
  fixture.status.error = 'USB camera permission denied';
  const unavailable = [];
  await assert.rejects(
    openCameraSource({
      environment: fixture.environment,
      onUnavailable: (error) => unavailable.push(error),
    }),
  );
  assert.equal(fixture.calls.builtin.length, 0);
  assert.equal(fixture.calls.draw.length, 0);
  assert.equal(unavailable.length, 0);
});

test('UVC stop closes native capture once, stops its track and cancels further polling', async () => {
  const fixture = cameraEnvironment();
  const source = await openCameraSource({ environment: fixture.environment });
  const fetchCount = fixture.calls.fetch.length;
  source.stop();
  source.stop();
  for (let index = 0; index < 4; index++) {
    fixture.runNextTimer();
    await flush();
  }
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.canvasTrack.stopped, 1);
  assert.equal(fixture.calls.fetch.length, fetchCount);
  assert.equal(fixture.timers.size, 0);
  assert.throws(() => source.assertFresh());
});

test('disconnecting UVC invalidates capture and notifies the owner only once', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  fixture.status.attached = false;
  fixture.status.state = 'disconnected';
  await until(fixture, () => unavailable.length > 0, 'UVC disconnect was not reported');
  assert.throws(() => source.assertFresh());
  assert.throws(() => source.assertFresh());
  source.stop();
  await flush();
  assert.equal(unavailable.length, 1);
  assert.equal(fixture.calls.builtin.length, 0);
});

test('a stale native JPEG cannot remain capturable merely because its video stays nonblack', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  fixture.status.frame_age_ms = 2501;
  await until(fixture, () => unavailable.length > 0, 'stale native frame was not rejected');
  assert.throws(() => source.assertFresh());
  assert.equal(fixture.calls.draw.length, 1);
  assert.equal(fixture.calls.builtin.length, 0);
  source.stop();
});

test('repeated frame IDs cannot reset freshness when native frame-age metadata stops updating', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  fixture.advance(2600);
  await until(fixture, () => unavailable.length > 0, 'a frozen frame ID was treated as fresh');
  assert.throws(() => source.assertFresh());
  assert.equal(fixture.calls.draw.length, 1);
  source.stop();
});

test('a native generation change never paints frames from a different acquisition', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  fixture.status.generation = '8';
  fixture.status.frame_id = 2;
  await until(fixture, () => unavailable.length > 0, 'changed native generation was not rejected');
  assert.equal(fixture.calls.draw.length, 1);
  assert.throws(() => source.assertFresh());
  source.stop();
});

test('a native frame endpoint cannot redirect the camera adapter to another origin', async () => {
  const fixture = cameraEnvironment();
  fixture.status.frame_url = 'https://unrelated.invalid/native-camera/frame.jpg';
  await assert.rejects(openCameraSource({ environment: fixture.environment }));
  assert.equal(fixture.calls.fetch.length, 0);
  assert.equal(fixture.calls.builtin.length, 0);
});

test('an already-cancelled camera request never opens native or browser capture', async () => {
  const fixture = cameraEnvironment();
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    openCameraSource({ signal: controller.signal, environment: fixture.environment }),
    (error) => error.name === 'AbortError',
  );
  assert.equal(fixture.calls.open, 0);
  assert.equal(fixture.calls.builtin.length, 0);
});

test('cancelling while waiting for USB permission wakes and clears the startup poll', async () => {
  const fixture = cameraEnvironment({ openingState: 'permission' });
  const controller = new AbortController();
  const pending = openCameraSource({ signal: controller.signal, environment: fixture.environment });
  const rejected = assert.rejects(pending, (error) => error.name === 'AbortError');
  await until(fixture, () => fixture.timers.size > 0, 'permission wait did not start');
  controller.abort();
  await rejected;
  await flush();
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.calls.fetch.length, 0);
  assert.equal(fixture.timers.size, 0);
});

test('native background release rejects an unopened permission request without waiting for the startup timeout', async () => {
  const fixture = cameraEnvironment({ openingState: 'permission' });
  const pending = openCameraSource({ environment: fixture.environment });
  const rejected = assert.rejects(pending, /后台|前台/);
  await until(fixture, () => fixture.timers.size > 0, 'permission wait did not start');
  fixture.status.state = 'idle';
  fixture.status.error_code = 'background';
  fixture.status.error = '应用已进入后台';
  fixture.runNextTimer();
  await rejected;
  assert.equal(fixture.calls.open, 1);
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.calls.fetch.length, 0);
  assert.equal(fixture.calls.draw.length, 0);
  assert.equal(fixture.timers.size, 0);
  fixture.status.state = 'streaming';
  await flush();
  assert.equal(fixture.calls.draw.length, 0, 'A late grant cannot revive a backgrounded opening');
});

test('granting pending USB permission resolves the original opening generation without reopening', async () => {
  const fixture = cameraEnvironment({ openingState: 'permission' });
  const pending = openCameraSource({ environment: fixture.environment });
  await until(fixture, () => fixture.timers.size > 0, 'permission wait did not start');
  const generation = fixture.status.generation;
  fixture.status.state = 'streaming';
  fixture.runNextTimer();
  const source = await pending;
  assert.equal(fixture.status.generation, generation);
  assert.equal(fixture.calls.open, 1);
  assert.equal(fixture.calls.close, 0);
  assert.equal(source.stream.getVideoTracks().length, 1);
  source.stop();
});

test('a JPEG finishing decode after USB acquisition changed cannot publish the old frame', async () => {
  const fixture = cameraEnvironment();
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  const pending = openCameraSource({ environment: fixture.environment });
  const rejected = assert.rejects(pending);
  await until(fixture, () => fixture.calls.decode.length === 1, 'first-frame decode did not start');
  fixture.status.generation = '8';
  decoded.resolve();
  await rejected;
  assert.equal(fixture.calls.draw.length, 0);
  assert.equal(fixture.calls.capture.length, 0);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(fixture.calls.builtin.length, 0);
});

test('a JPEG response belonging to another native generation is rejected before decode', async () => {
  const fixture = cameraEnvironment();
  fixture.environment.fetch = async (url, options) => {
    fixture.calls.fetch.push({ url: String(url), options });
    return { ...fixture.response(), headers: { get: () => '8' } };
  };
  await assert.rejects(openCameraSource({ environment: fixture.environment }));
  assert.equal(fixture.calls.decode.length, 0);
  assert.equal(fixture.calls.draw.length, 0);
  assert.equal(fixture.calls.builtin.length, 0);
});

test('stopping during a later frame fetch aborts I/O and prevents late painting or notifications', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  const fetched = deferred();
  fixture.environment.fetch = async (url, options) => {
    fixture.calls.fetch.push({ url: String(url), options });
    return fetched.promise;
  };
  fixture.status.frame_id = 2;
  await until(fixture, () => fixture.calls.fetch.length === 2, 'next-frame fetch did not start');
  const fetchSignal = fixture.calls.fetch[1].options.signal;
  source.stop();
  assert.equal(fetchSignal.aborted, true);
  fetched.resolve(fixture.response());
  await flush();
  assert.equal(fixture.calls.draw.length, 1);
  assert.equal(fixture.canvasTrack.stopped, 1);
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.timers.size, 0);
  assert.equal(unavailable.length, 0);
});

test('explicit builtin recovery remains available when the optional native bridge has malformed status', async () => {
  const fixture = cameraEnvironment();
  fixture.environment.PaperCamera.status = () => '{invalid native status';
  const source = await openCameraSource({
    preference: 'builtin',
    environment: fixture.environment,
  });
  assert.equal(source.kind, 'builtin');
  assert.equal(fixture.calls.open, 0);
  assert.equal(fixture.calls.fetch.length, 0);
  source.stop();
});

const landscapePixels = [
  ['a', 'b', 'c'],
  ['d', 'e', 'f'],
];
const rotatedPixels = new Map([
  [0, landscapePixels],
  [
    90,
    [
      ['d', 'a'],
      ['e', 'b'],
      ['f', 'c'],
    ],
  ],
  [
    180,
    [
      ['f', 'e', 'd'],
      ['c', 'b', 'a'],
    ],
  ],
  [
    270,
    [
      ['c', 'f'],
      ['b', 'e'],
      ['a', 'd'],
    ],
  ],
]);

for (const rotation of [0, 90, 180, 270]) {
  test(`UVC ${rotation}-degree rotation publishes actual oriented pixels and matching track dimensions`, async () => {
    const fixture = cameraEnvironment({ width: 3, height: 2, framePixels: landscapePixels });
    const source = await openCameraSource({ rotation, environment: fixture.environment });
    const expected = rotatedPixels.get(rotation);
    assert.deepEqual(fixture.canvas.pixels, expected);
    assert.deepEqual(source.stream.getVideoTracks()[0].getSettings(), {
      width: expected[0].length,
      height: expected.length,
    });
    assert.equal(source.rotation, rotation);
    assert.equal(source.canRotate, true);
    assert.equal(source.stream.getVideoTracks().length, 1);
    assert.equal(source.stream.getAudioTracks().length, 0);
    assert.equal(fixture.canvasTrack.requestedFrames, 1);
    assert.deepEqual(fixture.calls.capture, [10]);
    assert.deepEqual(fixture.calls.transforms.at(-1), [1, 0, 0, 1, 0, 0]);
    source.stop();
    assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  });
}

test('live UVC rotation repaints the current frame immediately without replacing its stream or USB session', async () => {
  const fixture = cameraEnvironment({ width: 3, height: 2, framePixels: landscapePixels });
  const source = await openCameraSource({ environment: fixture.environment });
  const stream = source.stream;
  const track = stream.getVideoTracks()[0];
  assert.equal(source.updateRotation, source.setRotation);
  for (const rotation of [90, 180, 270, 0]) {
    const expected = rotatedPixels.get(rotation);
    assert.deepEqual(source.setRotation(rotation), {
      rotation,
      width: expected[0].length,
      height: expected.length,
    });
    assert.deepEqual(fixture.canvas.pixels, expected);
    assert.equal(source.stream, stream);
    assert.equal(source.stream.getVideoTracks()[0], track);
    source.assertFresh();
  }
  assert.equal(fixture.calls.open, 1);
  assert.equal(fixture.calls.fetch.length, 1);
  assert.equal(fixture.calls.decode.length, 1);
  assert.deepEqual(fixture.calls.capture, [10]);
  assert.equal(track.requestedFrames, 5);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 0);
  source.setRotation(360);
  assert.equal(fixture.calls.draw.length, 5, 'the same orientation must not repaint');
  assert.equal(track.requestedFrames, 5);
  source.stop();
  source.stop();
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(track.stopped, 1);
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.timers.size, 0);
  assert.throws(() => source.setRotation(90));
  assert.equal(fixture.calls.draw.length, 5);
});

test('a JPEG decoded after a live angle change uses the newest orientation and releases the prior bitmap', async () => {
  const fixture = cameraEnvironment({ width: 3, height: 2, framePixels: landscapePixels });
  const source = await openCameraSource({ rotation: 90, environment: fixture.environment });
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  fixture.setPixels([
    ['A', 'B', 'C'],
    ['D', 'E', 'F'],
  ]);
  fixture.status.frame_id = 2;
  await until(fixture, () => fixture.calls.decode.length === 2, 'next JPEG decode did not start');
  source.setRotation(270);
  assert.deepEqual(fixture.canvas.pixels, rotatedPixels.get(270));
  decoded.resolve();
  await flush();
  assert.deepEqual(fixture.canvas.pixels, [
    ['C', 'F'],
    ['B', 'E'],
    ['A', 'D'],
  ]);
  assert.equal(source.rotation, 270);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(fixture.calls.decode[1].bitmap.closed, 0);
  assert.equal(fixture.calls.capture.length, 1);
  assert.equal(fixture.canvasTrack.requestedFrames, 3);
  source.stop();
  assert.equal(fixture.calls.decode[1].bitmap.closed, 1);
});

test('reorienting one acquired UVC frame cannot extend its freshness deadline', async () => {
  const fixture = cameraEnvironment();
  const source = await openCameraSource({ environment: fixture.environment });
  fixture.advance(2400);
  source.setRotation(90);
  fixture.advance(101);
  assert.throws(() => source.assertFresh());
  assert.throws(() => source.setRotation(180));
  assert.equal(source.rotation, 90);
  assert.equal(fixture.calls.draw.length, 2);
  assert.equal(fixture.canvasTrack.requestedFrames, 2);
  source.stop();
});

test('native stale metadata blocks UVC rotation before dimensions or pixels change', async () => {
  const fixture = cameraEnvironment();
  const source = await openCameraSource({ environment: fixture.environment });
  fixture.status.frame_age_ms = 2501;
  assert.throws(() => source.setRotation(90));
  assert.equal(source.rotation, 0);
  assert.equal(fixture.canvas.width, 1280);
  assert.equal(fixture.canvas.height, 720);
  assert.equal(fixture.calls.draw.length, 1);
  source.stop();
});

test('stop during a later rotated-frame decode closes retained and late bitmaps without painting', async () => {
  const fixture = cameraEnvironment();
  const source = await openCameraSource({ rotation: 90, environment: fixture.environment });
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  fixture.status.frame_id = 2;
  await until(fixture, () => fixture.calls.decode.length === 2, 'next JPEG decode did not start');
  source.stop();
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  decoded.resolve();
  await flush();
  assert.equal(fixture.calls.decode[1].bitmap.closed, 1);
  assert.equal(fixture.calls.draw.length, 1);
  assert.equal(fixture.canvasTrack.requestedFrames, 1);
  assert.equal(fixture.canvasTrack.stopped, 1);
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.timers.size, 0);
});

test('rotation does not disguise a native source-resolution change as a valid frame', async () => {
  const fixture = cameraEnvironment({ width: 3, height: 2, framePixels: landscapePixels });
  const unavailable = [];
  const source = await openCameraSource({
    rotation: 90,
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  fixture.status.width = 2;
  fixture.status.height = 3;
  fixture.status.frame_id = 2;
  await until(fixture, () => unavailable.length === 1, 'source-resolution change was not rejected');
  assert.equal(fixture.calls.draw.length, 1);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(fixture.calls.decode[1].bitmap.closed, 1);
  assert.equal(fixture.canvasTrack.stopped, 1);
  assert.throws(() => source.setRotation(0));
});

test('invalid camera angles fail before capture and do not alter an existing stream', async () => {
  const fixture = cameraEnvironment();
  await assert.rejects(
    openCameraSource({ rotation: 45, environment: fixture.environment }),
    RangeError,
  );
  assert.equal(fixture.calls.open, 0);
  assert.equal(fixture.calls.fetch.length, 0);
  const source = await openCameraSource({ environment: fixture.environment });
  for (const angle of [45, NaN, Infinity, '90', null])
    assert.throws(() => source.setRotation(angle), RangeError);
  assert.equal(source.rotation, 0);
  assert.equal(fixture.calls.draw.length, 1);
  assert.deepEqual(source.setRotation(-90), { rotation: 270, width: 720, height: 1280 });
  source.stop();
});

test('a remembered UVC angle does not silently rotate or wrap the builtin stream', async () => {
  const fixture = cameraEnvironment({ native: false });
  const source = await openCameraSource({ rotation: 90, environment: fixture.environment });
  assert.equal(source.stream, fixture.builtinStream);
  assert.equal(source.canRotate, false);
  assert.equal(source.rotation, 0);
  assert.equal(fixture.calls.capture.length, 0);
  assert.throws(() => source.setRotation(90));
  assert.deepEqual(source.setRotation(0), { rotation: 0, width: 1280, height: 960 });
  source.stop();
  assert.equal(fixture.builtinTrack.stopped, 1);
});

test('a rotation draw failure invalidates the stream and releases its retained native frame', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  fixture.canvas.getContext('2d').drawImage = () => {
    throw new Error('Synthetic lost drawing context');
  };
  assert.throws(() => source.setRotation(90), /lost drawing context/);
  assert.throws(() => source.assertFresh());
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.canvasTrack.stopped, 1);
  assert.equal(fixture.timers.size, 0);
  assert.equal(unavailable.length, 1);
  assert.deepEqual(fixture.calls.transforms.at(-1), [1, 0, 0, 1, 0, 0]);
  source.stop();
  assert.equal(unavailable.length, 1);
});

for (const [initialAge, decodeDelay] of [
  [0, 3000],
  [2400, 200],
]) {
  test(`a JPEG aged ${initialAge}ms before a ${decodeDelay}ms decode cannot borrow a newer native frame's freshness`, async () => {
    const fixture = cameraEnvironment();
    fixture.status.frame_age_ms = initialAge;
    const decoded = deferred();
    const originalDecode = fixture.environment.createImageBitmap;
    fixture.environment.createImageBitmap = async (blob) => {
      const bitmap = await originalDecode(blob);
      await decoded.promise;
      return bitmap;
    };
    const pending = openCameraSource({ rotation: 90, environment: fixture.environment });
    const rejected = assert.rejects(pending);
    await until(
      fixture,
      () => fixture.calls.decode.length === 1,
      'first JPEG decode did not start',
    );
    fixture.advance(decodeDelay);
    fixture.status.frame_id = 2;
    fixture.status.frame_age_ms = 0;
    decoded.resolve();
    await rejected;
    assert.equal(fixture.calls.draw.length, 0);
    assert.equal(fixture.calls.capture.length, 0);
    assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
    assert.equal(fixture.calls.close, 1);
    assert.equal(fixture.timers.size, 0);
  });
}

test('a retained bitmap keeps its original age and decode cost when newer native frames arrive', async () => {
  const fixture = cameraEnvironment();
  fixture.status.frame_age_ms = 1800;
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  const pending = openCameraSource({ environment: fixture.environment });
  await until(fixture, () => fixture.calls.decode.length === 1, 'first JPEG decode did not start');
  fixture.advance(500);
  fixture.status.frame_id = 2;
  fixture.status.frame_age_ms = 0;
  decoded.resolve();
  const source = await pending;
  source.setRotation(90);
  fixture.advance(201);
  assert.throws(() => source.assertFresh());
  assert.throws(() => source.setRotation(180));
  assert.equal(source.rotation, 90);
  assert.equal(fixture.calls.draw.length, 2);
  assert.equal(fixture.canvasTrack.requestedFrames, 2);
  source.stop();
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
});

test('an expired in-flight frame is discarded and closes the older retained rotated frame', async () => {
  const fixture = cameraEnvironment();
  const unavailable = [];
  const source = await openCameraSource({
    environment: fixture.environment,
    onUnavailable: (error) => unavailable.push(error),
  });
  const decoded = deferred();
  const originalDecode = fixture.environment.createImageBitmap;
  fixture.environment.createImageBitmap = async (blob) => {
    const bitmap = await originalDecode(blob);
    await decoded.promise;
    return bitmap;
  };
  fixture.status.frame_id = 2;
  await until(fixture, () => fixture.calls.decode.length === 2, 'next JPEG decode did not start');
  source.setRotation(90);
  fixture.advance(3000);
  fixture.status.frame_id = 3;
  fixture.status.frame_age_ms = 0;
  assert.throws(() => source.setRotation(180));
  decoded.resolve();
  await flush();
  assert.equal(fixture.calls.draw.length, 2);
  assert.equal(fixture.calls.decode[0].bitmap.closed, 1);
  assert.equal(fixture.calls.decode[1].bitmap.closed, 1);
  assert.equal(fixture.canvasTrack.stopped, 1);
  assert.equal(fixture.calls.close, 1);
  assert.equal(fixture.timers.size, 0);
  assert.equal(unavailable.length, 1);
});
