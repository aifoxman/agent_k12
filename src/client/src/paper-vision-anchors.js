// Optional local evidence belongs to these exact JPEG bytes, never another frame.
export function recognizeHintAnchors(
  image,
  { signal, isCurrent = () => true, timeoutMs = 2500, environment = globalThis } = {},
) {
  const bridge = environment.PaperVision;
  if (
    !bridge?.recognizeAnchors ||
    !environment.crypto?.subtle ||
    !environment.createImageBitmap ||
    signal?.aborted ||
    !isCurrent()
  )
    return Promise.resolve(null);
  return new Promise((resolve) => {
    const id = environment.crypto.randomUUID().replaceAll('-', '');
    let settled = false;
    let metadata;
    const finish = (value, cancel = false) => {
      if (settled) return;
      settled = true;
      environment.clearTimeout(timer);
      environment.removeEventListener('paper-vision-anchors', receive);
      signal?.removeEventListener('abort', abort);
      if (cancel) {
        try {
          bridge.cancelAnchors?.(id);
        } catch {
          /* Optional native cleanup. */
        }
      }
      resolve(value);
    };
    const abort = () => finish(null, true);
    const receive = (event) => {
      const result = event.detail;
      if (result?.id !== id) return;
      if (!isCurrent() || signal?.aborted) return finish(null, true);
      if (
        !metadata ||
        result.error ||
        result.image_sha256 !== metadata.image_sha256 ||
        result.width !== metadata.width ||
        result.height !== metadata.height
      )
        return finish(null);
      const anchors = (Array.isArray(result.anchors) ? result.anchors : [])
        .slice(0, 120)
        .filter(
          (anchor) =>
            anchor !== null &&
            typeof anchor === 'object' &&
            typeof anchor.id === 'string' &&
            anchor.id.length > 0 &&
            anchor.id.length <= 80 &&
            ['element', 'line'].includes(anchor.level) &&
            typeof anchor.text === 'string' &&
            anchor.text.trim() &&
            [...anchor.text].length <= 80 &&
            Number.isFinite(anchor.confidence) &&
            anchor.confidence >= 0.9 &&
            anchor.confidence <= 1 &&
            Array.isArray(anchor.box) &&
            anchor.box.length === 4 &&
            anchor.box.every((value) => Number.isFinite(value) && value >= 0 && value <= 1) &&
            anchor.box[2] > 0 &&
            anchor.box[3] > 0 &&
            anchor.box[0] + anchor.box[2] <= 1 &&
            anchor.box[1] + anchor.box[3] <= 1,
        )
        .map(({ id: anchorId, level, text, box, confidence }) => ({
          id: anchorId,
          level,
          text,
          box: [...box],
          confidence,
        }));
      const payload = anchors.length ? { ...metadata, anchors } : null;
      finish(
        payload && new TextEncoder().encode(JSON.stringify(payload)).length <= 49152
          ? payload
          : null,
      );
    };
    const timer = environment.setTimeout(abort, Math.min(2500, Math.max(1, timeoutMs)));
    environment.addEventListener('paper-vision-anchors', receive);
    signal?.addEventListener('abort', abort, { once: true });
    void Promise.all([
      image.arrayBuffer(),
      environment.createImageBitmap(image).then((bitmap) => {
        const size = { width: bitmap.width, height: bitmap.height };
        bitmap.close();
        return size;
      }),
    ])
      .then(async ([bytes, size]) => {
        const digest = await environment.crypto.subtle.digest('SHA-256', bytes);
        if (settled || signal?.aborted || !isCurrent()) return finish(null, true);
        if (
          !Number.isSafeInteger(size.width) ||
          size.width < 1 ||
          !Number.isSafeInteger(size.height) ||
          size.height < 1
        )
          return finish(null);
        metadata = {
          image_sha256: [...new Uint8Array(digest)]
            .map((byte) => byte.toString(16).padStart(2, '0'))
            .join(''),
          ...size,
        };
        const data = new Uint8Array(bytes);
        let binary = '';
        for (let index = 0; index < data.length; index += 16384)
          binary += String.fromCharCode(...data.subarray(index, index + 16384));
        if (settled || !isCurrent()) return finish(null, true);
        bridge.recognizeAnchors(environment.btoa(binary), id);
      })
      .catch(() => finish(null, true));
  });
}
