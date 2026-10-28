export function cameraRotation(value) {
  const angle = Number(value);
  return Number.isFinite(angle) && angle % 90 === 0 ? ((angle % 360) + 360) % 360 : 0;
}

export function canRotateCamera({ stage, preference, kind, hasBridge }) {
  if (['home', 'paused', 'done'].includes(stage)) return hasBridge && preference !== 'builtin';
  return ['preview', 'setup'].includes(stage) && kind === 'uvc';
}

// Canvas capture streams retain their track when their source size changes.
// Do not capture a new orientation until the video element has caught up.
export function waitForCameraDimensions(video, width, height, { signal, timeout = 2500 } = {}) {
  return new Promise((resolve, reject) => {
    let timer;
    let frameTimer;
    let frameRequest;
    let frameReady = false;
    let presentedFrames = 0;
    const finish = (error) => {
      clearTimeout(timer);
      clearTimeout(frameTimer);
      if (frameRequest !== undefined) video.cancelVideoFrameCallback?.(frameRequest);
      video.removeEventListener('resize', measure);
      video.removeEventListener('loadedmetadata', measure);
      signal?.removeEventListener('abort', abort);
      if (error) reject(error);
      else resolve();
    };
    const measure = () => {
      if (frameReady && video.videoWidth === width && video.videoHeight === height) finish();
    };
    const abort = () => finish(new DOMException('相机已关闭', 'AbortError'));
    if (signal?.aborted) return abort();
    video.addEventListener('resize', measure);
    video.addEventListener('loadedmetadata', measure);
    signal?.addEventListener('abort', abort, { once: true });
    timer = setTimeout(() => finish(new Error('相机方向仍在调整，请重新打开预览')), timeout);
    const presented = (_now, metadata) => {
      if (
        video.requestVideoFrameCallback &&
        metadata &&
        (metadata.width !== width || metadata.height !== height)
      ) {
        presentedFrames = 0;
        frameRequest = video.requestVideoFrameCallback(presented);
        return;
      }
      if (video.requestVideoFrameCallback && ++presentedFrames < 2) {
        frameRequest = video.requestVideoFrameCallback(presented);
        return;
      }
      frameReady = true;
      measure();
    };
    if (video.requestVideoFrameCallback) frameRequest = video.requestVideoFrameCallback(presented);
    else frameTimer = setTimeout(presented, 200);
  });
}
