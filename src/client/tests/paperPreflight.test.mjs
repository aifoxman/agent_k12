import assert from 'node:assert/strict';
import test from 'node:test';
import { assessPaperPixels } from '../src/paperPreflight.js';

function pixels(width, height, valueAt) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = valueAt(x, y);
      data[i + 3] = 255;
    }
  }
  return data;
}

test('local preflight stops only obvious camera failures', () => {
  const width = 128;
  const height = 96;
  assert.equal(assessPaperPixels(pixels(width, height, () => 0), width, height).issue,
    'dark');
  assert.equal(assessPaperPixels(pixels(width, height, () => 255), width, height).issue,
    'washed_out');
  assert.equal(assessPaperPixels(
    pixels(width, height, (x, y) => x > 8 && x < 120 && y % 12 === 0 ? 20 : 225),
    width, height).issue, '');
});

test('blur advisory does not become a hard rejection', () => {
  const width = 128;
  const height = 96;
  const result = assessPaperPixels(pixels(width, height, (x) => 40 + x), width, height);
  assert.equal(result.issue, '');
  assert.equal(typeof result.maybeBlurred, 'boolean');
});
