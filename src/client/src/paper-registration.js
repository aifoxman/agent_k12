// A small, dependency-free page matcher. Coordinates and homographies are
// normalized to the photograph, never to the DOM or the camera preview crop.
export const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];

export function project(matrix, x, y) {
  const z = matrix[6] * x + matrix[7] * y + matrix[8];
  if (Math.abs(z) < 0.1) throw new Error('Invalid page mapping');
  return [
    (matrix[0] * x + matrix[1] * y + matrix[2]) / z,
    (matrix[3] * x + matrix[4] * y + matrix[5]) / z,
  ];
}

export function projectBox(matrix, [x, y, w, h]) {
  const points = [
    [x, y],
    [x + w, y],
    [x + w, y + h],
    [x, y + h],
  ].map(([a, b]) => project(matrix, a, b));
  const left = Math.min(...points.map((p) => p[0]));
  const top = Math.min(...points.map((p) => p[1]));
  const right = Math.max(...points.map((p) => p[0]));
  const bottom = Math.max(...points.map((p) => p[1]));
  if (left < 0 || top < 0 || right > 1 || bottom > 1) return null;
  return [left, top, right - left, bottom - top];
}

export async function paperPixels(blob) {
  const bitmap = await createImageBitmap(blob);
  try {
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 480 / Math.max(bitmap.width, bitmap.height));
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const data = new Uint8Array(canvas.width * canvas.height);
    for (let i = 0; i < data.length; i++)
      data[i] = Math.round(0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2]);
    return { width: canvas.width, height: canvas.height, data };
  } finally {
    bitmap.close();
  }
}

function pixel(image, x, y) {
  return image.data[
    Math.max(0, Math.min(image.height - 1, Math.round(y))) * image.width +
      Math.max(0, Math.min(image.width - 1, Math.round(x)))
  ];
}

// Photometric verification is deliberately separate from feature fitting.
// Repeated question numbers, flat paper and notebook rules cannot pass it.
export function pageAgreement(reference, current, matrix, box = [0, 0, 1, 1]) {
  let tested = 0,
    localTested = 0,
    localMatched = 0;
  const matched = [];
  const [qx, qy, qw, qh] = box;
  for (let iy = 2; iy < 39; iy += 2)
    for (let ix = 2; ix < 39; ix += 2) {
      const x = ix / 40,
        y = iy / 40,
        first = [],
        second = [];
      for (const dy of [-3, 0, 3])
        for (const dx of [-3, 0, 3]) {
          const px = x + dx / reference.width,
            py = y + dy / reference.height;
          const [u, v] = project(matrix, px, py);
          if (u <= 0.01 || u >= 0.99 || v <= 0.01 || v >= 0.99) continue;
          first.push(pixel(reference, px * (reference.width - 1), py * (reference.height - 1)));
          second.push(pixel(current, u * (current.width - 1), v * (current.height - 1)));
        }
      if (first.length !== 9 || Math.max(...first) - Math.min(...first) < 65) continue;
      const a = first.reduce((s, v) => s + v, 0) / 9,
        b = second.reduce((s, v) => s + v, 0) / 9;
      const va = first.reduce((s, v) => s + (v - a) ** 2, 0);
      const vb = second.reduce((s, v) => s + (v - b) ** 2, 0);
      const correlation =
        first.reduce((s, v, i) => s + (v - a) * (second[i] - b), 0) /
        Math.max(1, Math.sqrt(va * vb));
      const local = x >= qx - 0.02 && x <= qx + qw + 0.02 && y >= qy - 0.02 && y <= qy + qh + 0.02;
      tested++;
      localTested += Number(local);
      if (correlation >= 0.82) {
        matched.push([x, y]);
        localMatched += Number(local);
      }
    }
  const coverage = matched.length
    ? Math.max(...matched.map((p) => p[0])) - Math.min(...matched.map((p) => p[0])) >= 0.25 &&
      Math.max(...matched.map((p) => p[1])) - Math.min(...matched.map((p) => p[1])) >= 0.25
    : false;
  return {
    reliable:
      tested >= 18 &&
      matched.length >= 14 &&
      matched.length / tested >= 0.62 &&
      localTested >= 4 &&
      localMatched / localTested >= 0.65 &&
      coverage,
    matched: matched.length,
    tested,
  };
}

function features(image) {
  const { width: w, height: h, data } = image;
  const candidates = [];
  for (let y = 15; y < h - 15; y += 2)
    for (let x = 15; x < w - 15; x += 2) {
      let xx = 0,
        yy = 0,
        xy = 0;
      for (let dy = -2; dy <= 2; dy++)
        for (let dx = -2; dx <= 2; dx++) {
          const p = (y + dy) * w + x + dx;
          const gx = data[p + 1] - data[p - 1],
            gy = data[p + w] - data[p - w];
          xx += gx * gx;
          yy += gy * gy;
          xy += gx * gy;
        }
      const score = xx * yy - xy * xy - 0.05 * (xx + yy) ** 2;
      if (score > 2e7) candidates.push({ x, y, score });
    }
  candidates.sort((a, b) => b.score - a.score);
  const selected = [],
    cells = new Map();
  for (const point of candidates) {
    const cell = `${Math.floor(point.x / 48)}:${Math.floor(point.y / 48)}`;
    if (
      (cells.get(cell) || 0) >= 7 ||
      selected.some((p) => Math.hypot(p.x - point.x, p.y - point.y) < 6)
    )
      continue;
    selected.push(point);
    cells.set(cell, (cells.get(cell) || 0) + 1);
    if (selected.length >= 320) break;
  }
  return selected.map(({ x, y }) => {
    let mx = 0,
      my = 0;
    for (let dy = -7; dy <= 7; dy++)
      for (let dx = -7; dx <= 7; dx++) {
        if (dx * dx + dy * dy > 49) continue;
        const ink = 255 - data[(y + dy) * w + x + dx];
        mx += dx * ink;
        my += dy * ink;
      }
    const angle = Math.atan2(my, mx),
      c = Math.cos(angle),
      s = Math.sin(angle);
    const sample = (dx, dy) => pixel(image, x + c * dx - s * dy, y + s * dx + c * dy);
    const descriptor = new Uint32Array(4);
    let seed = 7381;
    const random = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return (seed % 21) - 10;
    };
    for (let bit = 0; bit < 128; bit++) {
      const ax = random(),
        ay = random(),
        bx = random(),
        by = random();
      if (sample(ax, ay) < sample(bx, by)) descriptor[bit >>> 5] |= 1 << (bit & 31);
    }
    return { point: [x / (w - 1), y / (h - 1)], descriptor };
  });
}

function distance(a, b) {
  let count = 0;
  for (let i = 0; i < 4; i++) {
    let value = (a[i] ^ b[i]) >>> 0;
    while (value) {
      value &= value - 1;
      count++;
    }
  }
  return count;
}

export function fitHomography(pairs) {
  const rows = [];
  for (const [[x, y], [u, v]] of pairs) {
    rows.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    rows.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  const normal = Array.from({ length: 8 }, (_, i) =>
    Array.from({ length: 9 }, (_, j) => rows.reduce((sum, row) => sum + row[i] * row[j], 0)),
  );
  for (let i = 0; i < 8; i++) {
    let best = i;
    for (let j = i + 1; j < 8; j++)
      if (Math.abs(normal[j][i]) > Math.abs(normal[best][i])) best = j;
    [normal[i], normal[best]] = [normal[best], normal[i]];
    const pivot = normal[i][i];
    if (Math.abs(pivot) < 1e-9) return null;
    for (let k = i; k < 9; k++) normal[i][k] /= pivot;
    for (let j = 0; j < 8; j++)
      if (j !== i) {
        const factor = normal[j][i];
        for (let k = i; k < 9; k++) normal[j][k] -= factor * normal[i][k];
      }
  }
  return [...normal.map((row) => row[8]), 1];
}

export function registerPaper(reference, current) {
  if (pageAgreement(reference, current, IDENTITY).reliable)
    return { matrix: [...IDENTITY], method: 'stationary' };
  const a = reference.features || (reference.features = features(reference));
  const b = features(current),
    pairs = [];
  for (const first of a) {
    let best = 129,
      second = 129,
      candidate = null;
    for (const other of b) {
      const value = distance(first.descriptor, other.descriptor);
      if (value < best) {
        second = best;
        best = value;
        candidate = other;
      } else if (value < second) second = value;
    }
    if (!candidate || best > 35 || best >= second * 0.72) continue;
    const reverse = a.reduce(
      (match, other) =>
        distance(other.descriptor, candidate.descriptor) <
        distance(match.descriptor, candidate.descriptor)
          ? other
          : match,
      first,
    );
    if (reverse === first) pairs.push([first.point, candidate.point]);
  }
  if (pairs.length < 10) return null;
  let seed = 617,
    winner = [];
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed;
  };
  for (let iteration = 0; iteration < 450; iteration++) {
    const indices = new Set();
    while (indices.size < 4) indices.add(random() % pairs.length);
    const matrix = fitHomography([...indices].map((i) => pairs[i]));
    if (!matrix) continue;
    try {
      const inliers = pairs.filter(([point, actual]) => {
        const predicted = project(matrix, ...point);
        return Math.hypot(predicted[0] - actual[0], predicted[1] - actual[1]) < 0.007;
      });
      if (inliers.length > winner.length) winner = inliers;
    } catch {
      /* Degenerate sample; another independent sample may fit. */
    }
  }
  if (winner.length < 10 || winner.length / pairs.length < 0.5) return null;
  const matrix = fitHomography(winner);
  try {
    return matrix && pageAgreement(reference, current, matrix).reliable
      ? { matrix, method: 'registered', inliers: winner.length }
      : null;
  } catch {
    return null;
  }
}
