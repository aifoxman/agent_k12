// Feature matching runs outside the UI thread so the frozen paper and ink can
// paint immediately. A cancelled gesture never gets a late worker result.
export function matchPaperIndex(index, pixels, strokes, signal) {
  if (!index || signal?.aborted) return Promise.resolve(null);
  return new Promise((resolve) => {
    const worker = new Worker(new URL('./paper-index-worker.js', import.meta.url), {
      type: 'module',
    });
    const finish = (binding) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      worker.terminate();
      resolve(binding);
    };
    const abort = () => finish(null);
    const timer = setTimeout(abort, 2500);
    signal?.addEventListener('abort', abort, { once: true });
    worker.onmessage = ({ data }) => finish(data.binding);
    worker.onerror = abort;
    worker.postMessage({ index, pixels, strokes });
  });
}
