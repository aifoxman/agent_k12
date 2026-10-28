// Keep one real video track for preview, paper captures and parent WebRTC.
// Native USB frames remain inside the app origin; no camera image leaves here.
const FRAME_MAX_AGE = 2500;
const OPEN_TIMEOUT = 45000;
const FRAME_INTERVAL = 120;

function abortError() {
  return new DOMException('相机已关闭', 'AbortError');
}

function nativeStatus(bridge) {
  const status = bridge.status();
  return typeof status === 'string' ? JSON.parse(status) : status;
}

function cameraRotation(value) {
  if (!Number.isSafeInteger(value) || value % 90 !== 0)
    throw new RangeError('相机旋转角度必须是 90 度的整数倍');
  return ((value % 360) + 360) % 360;
}

function rotatedFrame(width, height, rotation) {
  switch (rotation) {
    case 90:
      return { width: height, height: width, transform: [0, 1, -1, 0, height, 0] };
    case 180:
      return { width, height, transform: [-1, 0, 0, -1, width, height] };
    case 270:
      return { width: height, height: width, transform: [0, -1, 1, 0, 0, width] };
    default:
      return { width, height, transform: [1, 0, 0, 1, 0, 0] };
  }
}

export async function openCameraSource({
  preference = 'auto',
  rotation = 0,
  onUnavailable = () => {},
  signal,
  environment = globalThis,
} = {}) {
  if (signal?.aborted) throw abortError();
  let currentRotation = cameraRotation(rotation);
  const bridge = environment.PaperCamera;
  const attached = preference !== 'builtin' && bridge && nativeStatus(bridge)?.attached;
  if (preference === 'uvc' && !attached) throw new Error('未找到外接相机，请检查 USB 连接');
  if (preference === 'builtin' || !attached) {
    const stream = await environment.navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1280 },
        height: { ideal: 960 },
        aspectRatio: { ideal: 4 / 3 },
      },
      audio: false,
    });
    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      signal?.removeEventListener('abort', stop);
      stream.getTracks().forEach((track) => track.stop());
    };
    signal?.addEventListener('abort', stop, { once: true });
    if (signal?.aborted) {
      stop();
      throw abortError();
    }
    const assertFresh = () => {
      if (stopped || !stream.getVideoTracks().some((track) => track.readyState === 'live'))
        throw new Error('相机已关闭，请重新打开');
    };
    const setRotation = (angle) => {
      if (cameraRotation(angle) !== 0) throw new Error('当前相机不支持像素旋转，请使用外接相机');
      assertFresh();
      const { width, height } = stream.getVideoTracks()[0]?.getSettings?.() || {};
      return { rotation: 0, width, height };
    };
    return {
      stream,
      kind: 'builtin',
      label: '平板后置相机',
      canRotate: false,
      rotation: 0,
      setRotation,
      updateRotation: setRotation,
      stop,
      assertFresh,
    };
  }

  const canvas = environment.document.createElement('canvas');
  if (!canvas.captureStream || !environment.createImageBitmap)
    throw new Error('当前系统网页组件不支持外接相机，请更新系统 WebView');
  const context = canvas.getContext('2d', { alpha: false });
  if (!context) throw new Error('无法创建相机画面，请重试');
  const now = () => environment.performance?.now?.() ?? (environment.Date || Date).now();
  const fetchController = new (environment.AbortController || AbortController)();
  let stream = null;
  let stopped = false;
  let timer = null;
  let wake = null;
  let generation = null;
  let frameId = -1;
  let bitmapFreshUntil = 0;
  let currentBitmap = null;
  let sourceWidth = 0;
  let sourceHeight = 0;
  let failure = null;
  let opened = false;
  let rejectAborted;
  const aborted = new Promise((_, reject) => {
    rejectAborted = reject;
  });
  const stop = () => {
    if (stopped) return;
    stopped = true;
    environment.clearTimeout(timer);
    wake?.();
    fetchController.abort();
    stream?.getTracks().forEach((track) => track.stop());
    currentBitmap?.close?.();
    currentBitmap = null;
    signal?.removeEventListener('abort', abort);
    try {
      bridge.close();
    } catch {
      /* The activity may already have released USB. */
    }
  };
  const abort = () => {
    stop();
    rejectAborted(abortError());
  };
  const wait = () =>
    new Promise((resolve) => {
      wake = resolve;
      timer = environment.setTimeout(() => {
        wake = null;
        resolve();
      }, FRAME_INTERVAL);
    });
  const assertFresh = () => {
    if (failure) throw failure;
    if (stopped) throw new Error('外接相机已关闭，请重新打开');
    const state = nativeStatus(bridge);
    if (
      !stream?.getVideoTracks().some((track) => track.readyState === 'live') ||
      !state.attached ||
      state.state !== 'streaming' ||
      String(state.generation) !== generation ||
      !Number.isFinite(state.frame_age_ms) ||
      state.frame_age_ms > FRAME_MAX_AGE ||
      state.frame_age_ms < 0 ||
      !currentBitmap ||
      now() > bitmapFreshUntil
    )
      throw new Error('外接相机画面已中断，请检查 USB 连接后重新打开');
  };
  const render = (bitmap) => {
    const frame = rotatedFrame(bitmap.width, bitmap.height, currentRotation);
    // Resizing a capture canvas keeps its track while updating the encoded dimensions.
    // Never reuse the prior orientation's backing pixels after a quarter turn.
    if (canvas.width !== frame.width) canvas.width = frame.width;
    if (canvas.height !== frame.height) canvas.height = frame.height;
    context.setTransform(...frame.transform);
    try {
      context.drawImage(bitmap, 0, 0, bitmap.width, bitmap.height);
    } finally {
      context.setTransform(1, 0, 0, 1, 0, 0);
    }
  };
  const dimensions = () => ({
    rotation: currentRotation,
    width: canvas.width,
    height: canvas.height,
  });
  const setRotation = (angle) => {
    const nextRotation = cameraRotation(angle);
    assertFresh();
    if (!currentBitmap) throw new Error('外接相机还没有可用画面');
    if (nextRotation !== currentRotation) {
      try {
        currentRotation = nextRotation;
        render(currentBitmap);
        stream.getVideoTracks()[0]?.requestFrame?.();
      } catch (error) {
        unavailable(error);
        throw error;
      }
    }
    // Repainting one acquired frame must not advance frameId or its freshness deadline.
    // Consumers should wait for their video element's resize before a new snapshot.
    return dimensions();
  };
  const readFrame = async (starting) => {
    if (stopped) throw abortError();
    const observedAt = now();
    const state = nativeStatus(bridge);
    if (starting && state.state === 'idle' && state.error_code === 'background')
      throw new Error(state.error || '应用已离开前台，请重新打开相机');
    if (!state.attached || ['error', 'denied', 'disconnected', 'closed'].includes(state.state))
      throw new Error(state.error || '外接相机连接中断，请检查 USB 连接并允许相机访问');
    if (state.state !== 'streaming' || !state.frame_id) {
      if (!starting) assertFresh();
      return false;
    }
    if (
      !Number.isFinite(state.frame_age_ms) ||
      state.frame_age_ms > FRAME_MAX_AGE ||
      state.frame_age_ms < 0
    ) {
      if (starting) return false;
      throw new Error('外接相机没有传回新画面，请重新连接');
    }
    // A newer native status cannot rejuvenate the JPEG fetched by this request.
    // The endpoint may return a newer frame, but this conservative bound never
    // grants more time than the observed frame's age plus actual fetch/decode time.
    const freshUntil = observedAt + FRAME_MAX_AGE - state.frame_age_ms;
    const nextGeneration = String(state.generation);
    if (generation !== null && nextGeneration !== generation)
      throw new Error('外接相机已重新连接，请重新打开取景');
    if (state.frame_id <= frameId) {
      if (!starting) assertFresh();
      return false;
    }
    const origin = environment.location?.origin || 'https://appassets.androidplatform.net';
    const url = new URL(state.frame_url || '/native-camera/frame.jpg', origin);
    if (
      url.origin !== origin ||
      url.pathname !== '/native-camera/frame.jpg' ||
      url.username ||
      url.password
    )
      throw new Error('外接相机画面地址无效');
    url.searchParams.set('generation', nextGeneration);
    url.searchParams.set('frame', String(state.frame_id));
    const response = await environment.fetch(url.href, {
      cache: 'no-store',
      signal: fetchController.signal,
    });
    if (!response.ok) {
      if (starting && response.status === 503) return false;
      throw new Error('外接相机取景中断，请重新打开');
    }
    const headerGeneration = response.headers?.get('X-Paper-Camera-Generation');
    if (
      headerGeneration !== null &&
      headerGeneration !== undefined &&
      String(headerGeneration) !== nextGeneration
    )
      throw new Error('外接相机画面已失效，请重新打开');
    const bitmap = await environment.createImageBitmap(await response.blob());
    try {
      if (stopped || signal?.aborted) throw abortError();
      const current = nativeStatus(bridge);
      if (
        !current.attached ||
        current.state !== 'streaming' ||
        String(current.generation) !== nextGeneration ||
        !Number.isFinite(current.frame_age_ms) ||
        current.frame_age_ms > FRAME_MAX_AGE ||
        current.frame_age_ms < 0 ||
        now() > freshUntil
      )
        throw new Error('外接相机画面已失效，请重新打开');
      if (!bitmap.width || !bitmap.height) throw new Error('外接相机返回了空画面');
      if (sourceWidth && (bitmap.width !== sourceWidth || bitmap.height !== sourceHeight))
        throw new Error('外接相机分辨率发生变化，请重新打开');
      render(bitmap);
      if (now() > freshUntil) throw new Error('外接相机画面读取超时，请重新打开');
      sourceWidth = bitmap.width;
      sourceHeight = bitmap.height;
      generation = nextGeneration;
      frameId = state.frame_id;
      bitmapFreshUntil = freshUntil;
      if (!stream) {
        stream = canvas.captureStream(10);
        if (stream.getVideoTracks().length !== 1 || stream.getAudioTracks().length)
          throw new Error('外接相机视频轨道不可用');
      }
      stream.getVideoTracks()[0]?.requestFrame?.();
      const previousBitmap = currentBitmap;
      currentBitmap = bitmap;
      previousBitmap?.close?.();
      return true;
    } finally {
      if (bitmap !== currentBitmap) bitmap.close?.();
    }
  };
  const unavailable = (error) => {
    if (stopped) return;
    failure = error;
    stop();
    if (opened) onUnavailable(error);
  };
  const pump = async () => {
    if (stopped) return;
    try {
      await readFrame(false);
    } catch (error) {
      unavailable(error);
      return;
    }
    if (!stopped) timer = environment.setTimeout(pump, FRAME_INTERVAL);
  };
  signal?.addEventListener('abort', abort, { once: true });
  const start = async () => {
    if (signal?.aborted) throw abortError();
    bridge.open();
    const deadline = now() + OPEN_TIMEOUT;
    while (!stopped && now() < deadline) {
      if (await readFrame(true)) {
        opened = true;
        timer = environment.setTimeout(pump, FRAME_INTERVAL);
        return {
          stream,
          kind: 'uvc',
          label: 'USB 外接相机',
          canRotate: true,
          get rotation() {
            return currentRotation;
          },
          setRotation,
          updateRotation: setRotation,
          stop,
          assertFresh,
        };
      }
      await wait();
    }
    throw stopped ? abortError() : new Error('外接相机打开超时，请允许 USB 访问后重试');
  };
  try {
    return await Promise.race([start(), aborted]);
  } catch (error) {
    stop();
    throw error;
  }
}
