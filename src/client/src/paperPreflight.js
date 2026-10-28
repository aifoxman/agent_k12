/** Conservative local checks. Only an obvious camera failure stops the cloud check. */
export function assessPaperPixels(pixels, width, height) {
  const count = width * height;
  if (!count || pixels.length < count * 4) return { issue: '', maybeBlurred: false };
  const gray = new Uint8Array(count);
  let total = 0;
  let totalSquared = 0;
  let dark = 0;
  let white = 0;
  for (let i = 0; i < count; i++) {
    const p = i * 4;
    const value = Math.round(0.299 * pixels[p] + 0.587 * pixels[p + 1] + 0.114 * pixels[p + 2]);
    gray[i] = value;
    total += value;
    totalSquared += value * value;
    if (value < 24) dark++;
    if (value > 247) white++;
  }
  const mean = total / count;
  const deviation = Math.sqrt(Math.max(0, totalSquared / count - mean * mean));
  let strongEdges = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const edge = Math.abs(
        4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width],
      );
      if (edge > 32) strongEdges++;
    }
  }
  return {
    issue:
      dark / count > 0.97 || mean < 18
        ? 'dark'
        : white / count > 0.9999 && deviation < 2
          ? 'washed_out'
          : '',
    // This is only an advisory: a diagram or mostly blank answer area can lack text edges.
    maybeBlurred: deviation > 12 && strongEdges / count < 0.002,
  };
}

export function measurePaperFrame(video) {
  if (!video?.videoWidth) return { issue: '', maybeBlurred: false };
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 96;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return { issue: '', maybeBlurred: false };
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return assessPaperPixels(
    context.getImageData(0, 0, canvas.width, canvas.height).data,
    canvas.width,
    canvas.height,
  );
}

export function recognizePaperText(image) {
  if (!window.PaperVision?.recognize) return Promise.resolve(null);
  return new Promise((resolve) => {
    const id = crypto.randomUUID().replaceAll('-', '');
    const reader = new FileReader();
    let timer;
    const finish = (result) => {
      clearTimeout(timer);
      window.removeEventListener('paper-vision', onVision);
      resolve(result);
    };
    const onVision = (event) => {
      if (event.detail?.id === id) finish(event.detail);
    };
    window.addEventListener('paper-vision', onVision);
    timer = setTimeout(() => finish(null), 3000);
    reader.onerror = () => finish(null);
    reader.onload = () => {
      try {
        window.PaperVision.recognize(String(reader.result).split(',')[1], id);
      } catch {
        finish(null);
      }
    };
    reader.readAsDataURL(image);
  });
}
