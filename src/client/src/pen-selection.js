export function penSelectionBounds(strokes) {
  const points = strokes.flat();
  if (points.length < 2) return null;
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const left = Math.max(0, Math.min(...xs) - 0.012);
  const top = Math.max(0, Math.min(...ys) - 0.012);
  const right = Math.min(1, Math.max(...xs) + 0.012);
  const bottom = Math.min(1, Math.max(...ys) + 0.012);
  return [left, top, right - left, bottom - top];
}

export function penSelectionReady(strokes) {
  return (
    strokes.reduce(
      (total, stroke) =>
        total +
        stroke.slice(1).reduce((length, [x, y], index) => {
          const [previousX, previousY] = stroke[index];
          return length + Math.hypot(x - previousX, y - previousY);
        }, 0),
      0,
    ) >= 0.045
  );
}

export function createPenHintScheduler(onHint, delayMs = 1200, clock = globalThis) {
  let timer = null;
  let revision = 0;
  const clear = () => {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
  };
  const cancel = () => {
    revision += 1;
    clear();
  };
  return {
    cancel,
    change: cancel,
    revision: () => revision,
    finish(strokes) {
      clear();
      if (!penSelectionReady(strokes)) return;
      const current = revision;
      const copy = strokes.map((stroke) => stroke.map((point) => [...point]));
      timer = clock.setTimeout(() => {
        timer = null;
        if (revision === current) onHint(copy, current);
      }, delayMs);
    },
  };
}
