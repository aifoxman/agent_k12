import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createStallWatch,
  sampleStallWatch,
  settleAmbiguousPage,
  settleIdleCheckin,
  settleStallProbe,
} from '../src/stall-watch.js';

const width = 96;
const height = 72;

function paper(boxes = []) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const shade = boxes.reduce(
        (value, [left, top, right, bottom, boxShade]) =>
          x >= left && x < right && y >= top && y < bottom ? boxShade : value,
        255,
      );
      const index = (y * width + x) * 4;
      pixels[index] = pixels[index + 1] = pixels[index + 2] = shade;
      pixels[index + 3] = 255;
    }
  }
  return pixels;
}

function seedFocus(watch, working) {
  sampleStallWatch(watch, working, width, height, 0, 'standard');
  watch.focus = [0.2, 0.2, 0.25, 0.34];
  watch.focusSince = 1_000;
  watch.lastProgressAt = 1_000;
  watch.lastMotionAt = 1_000;
}

test('stalled question is only probed after both focus and idle thresholds', () => {
  const watch = createStallWatch();
  const blank = paper();
  const firstStroke = paper([[20, 20, 40, 40, 100]]);
  const secondStroke = paper([
    [20, 20, 40, 40, 100],
    [14, 24, 20, 34, 100],
  ]);
  const focus = paper([
    [20, 20, 40, 40, 100],
    [14, 24, 20, 34, 100],
    [40, 24, 46, 34, 100],
  ]);
  sampleStallWatch(watch, blank, width, height, 0, 'standard');
  sampleStallWatch(watch, firstStroke, width, height, 1_000, 'standard');
  assert.equal(watch.focus, null);
  sampleStallWatch(watch, secondStroke, width, height, 5_000, 'standard');
  sampleStallWatch(watch, focus, width, height, 10_000, 'standard');
  assert.ok(watch.focus);
  sampleStallWatch(watch, focus, width, height, 12_000, 'standard');
  assert.equal(
    sampleStallWatch(watch, focus, width, height, 14_000, 'standard').referenceRefresh,
    true,
  );
  assert.equal(sampleStallWatch(watch, focus, width, height, 309_000, 'standard').ready, false);
  const probe = sampleStallWatch(watch, focus, width, height, 312_000, 'standard');
  assert.equal(probe.ready, true);
  assert.equal(sampleStallWatch(watch, focus, width, height, 314_000, 'standard').ready, false);
  assert.equal(
    settleStallProbe(watch, probe.version, { found: true, effort: 'short' }, 'standard', 315_000),
    'offer',
  );
  assert.equal(sampleStallWatch(watch, focus, width, height, 600_000, 'standard').ready, false);
});

test('a still first page gets a cautious check-in after three minutes without a focus', () => {
  const watch = createStallWatch();
  const page = paper([[20, 20, 40, 40, 100]]);
  sampleStallWatch(watch, page, width, height, 1_000, 'standard');
  assert.equal(sampleStallWatch(watch, page, width, height, 180_999, 'standard').checkinReady, undefined);
  const checkin = sampleStallWatch(watch, page, width, height, 181_000, 'standard');
  assert.equal(checkin.checkinReady, true);
  assert.equal(sampleStallWatch(watch, page, width, height, 183_000, 'standard').checkinReady, undefined);
  assert.equal(settleIdleCheckin(watch, checkin.version, { offer: true }, 184_000), 'offer');
  assert.equal(sampleStallWatch(watch, page, width, height, 400_000, 'standard').checkinReady, undefined);
});

test('visible progress restarts the quiet-page window and a page turn does not inherit it', () => {
  const watch = createStallWatch();
  const page = paper([[20, 20, 40, 40, 100]]);
  sampleStallWatch(watch, page, width, height, 1_000, 'standard');
  const checkin = sampleStallWatch(watch, page, width, height, 181_000, 'standard');
  assert.equal(settleIdleCheckin(watch, checkin.version, { progress: true }, 182_000), 'rebaseline');
  assert.equal(sampleStallWatch(watch, page, width, height, 300_000, 'standard').checkinReady, undefined);
  assert.equal(sampleStallWatch(watch, page, width, height, 362_000, 'standard').checkinReady, true);
  assert.equal(settleIdleCheckin(watch, watch.version, { page_changed: true }, 363_000), 'rebaseline');
  assert.equal(watch.pageVersion, 1);
  assert.equal(sampleStallWatch(watch, page, width, height, 363_100, 'standard').checkinReady, undefined);
});

test('extended work waits longer and uncertain results remain silent', () => {
  const watch = createStallWatch();
  watch.focus = [0.2, 0.2, 0.25, 0.34];
  watch.focusSince = 1_000;
  watch.lastProgressAt = 1_000;
  watch.lastMotionAt = 1_000;
  watch.probePending = true;
  assert.equal(
    settleStallProbe(
      watch,
      watch.version,
      { found: true, effort: 'extended' },
      'standard',
      302_000,
    ),
    'deferred',
  );
  assert.equal(watch.deferUntil, 481_000);
  watch.probePending = true;
  assert.equal(
    settleStallProbe(watch, watch.version, { found: false }, 'standard', 482_000),
    'silent',
  );
  assert.equal(watch.silenced, true);
});

test('writing progress invalidates an in-flight candidate', () => {
  const watch = createStallWatch();
  const focus = paper([[20, 20, 40, 40, 100]]);
  const written = paper([
    [20, 20, 40, 40, 100],
    [40, 31, 50, 39, 100],
  ]);
  seedFocus(watch, focus);
  const version = watch.version;
  sampleStallWatch(watch, written, width, height, 10_000, 'standard');
  sampleStallWatch(watch, written, width, height, 12_000, 'standard');
  assert.equal(
    sampleStallWatch(watch, written, width, height, 14_000, 'standard').progress,
    undefined,
  );
  assert.equal(sampleStallWatch(watch, written, width, height, 24_000, 'standard').progress, true);
  assert.equal(
    settleStallProbe(watch, version, { found: true, effort: 'short' }, 'standard', 15_000),
    'stale',
  );
});

test('a passing arm away from the visible question does not retarget the question', () => {
  const watch = createStallWatch();
  const working = paper([[20, 20, 40, 40, 100]]);
  const blockedElsewhere = paper([
    [20, 20, 40, 40, 100],
    [65, 20, 80, 40, 40],
  ]);
  seedFocus(watch, working);
  const originalFocus = [...watch.focus];
  sampleStallWatch(watch, blockedElsewhere, width, height, 200_000, 'standard');
  sampleStallWatch(watch, working, width, height, 202_000, 'standard');
  sampleStallWatch(watch, working, width, height, 204_000, 'standard');
  sampleStallWatch(watch, working, width, height, 206_000, 'standard');
  assert.deepEqual(watch.focus, originalFocus);
  assert.equal(sampleStallWatch(watch, working, width, height, 302_000, 'standard').ready, true);
});

test('a broad arm on the other side of the page is not treated as a page turn', () => {
  const watch = createStallWatch();
  const working = paper([[20, 20, 40, 40, 100]]);
  const arm = paper([
    [20, 20, 40, 40, 100],
    [60, 0, 96, 60, 40],
  ]);
  seedFocus(watch, working);
  const originalPage = watch.pageVersion;
  sampleStallWatch(watch, arm, width, height, 200_000, 'standard');
  sampleStallWatch(watch, working, width, height, 202_000, 'standard');
  sampleStallWatch(watch, working, width, height, 204_000, 'standard');
  sampleStallWatch(watch, working, width, height, 206_000, 'standard');
  assert.equal(watch.pageVersion, originalPage);
  assert.equal(sampleStallWatch(watch, working, width, height, 302_000, 'standard').ready, true);
});

test('a brief broad obstruction does not replace the page or create an initial focus', () => {
  const watch = createStallWatch();
  const page = paper();
  const covered = paper([[10, 5, 86, 64, 40]]);
  sampleStallWatch(watch, page, width, height, 0, 'standard');
  sampleStallWatch(watch, covered, width, height, 2_000, 'standard');
  sampleStallWatch(watch, covered, width, height, 4_000, 'standard');
  sampleStallWatch(watch, page, width, height, 6_000, 'standard');
  sampleStallWatch(watch, page, width, height, 8_000, 'standard');
  sampleStallWatch(watch, page, width, height, 10_000, 'standard');
  assert.equal(watch.pageVersion, 0);
  assert.equal(watch.focus, null);
  assert.equal(watch.pendingPageAt, 0);
});

test('a broad settled image receives a page check instead of blocking help forever', () => {
  const watch = createStallWatch();
  const first = paper();
  const settled = paper([[12, 0, 84, 60, 145]]);
  sampleStallWatch(watch, first, width, height, 0, 'standard');
  for (let now = 2_000; now < 32_000; now += 2_000) {
    assert.equal(sampleStallWatch(watch, settled, width, height, now, 'standard').ambiguousPageReady, undefined);
  }
  const check = sampleStallWatch(watch, settled, width, height, 32_000, 'standard');
  assert.equal(check.ambiguousPageReady, true);
  assert.equal(sampleStallWatch(watch, settled, width, height, 34_000, 'standard').ambiguousPageReady, undefined);
  assert.equal(settleAmbiguousPage(watch, check.version, { offer: true }, 35_000), 'rebaseline');
  assert.equal(watch.observedSince, 0);
  assert.equal(watch.pageVersion, 0);
  assert.equal(sampleStallWatch(watch, settled, width, height, 180_000, 'standard').checkinReady, true);
});

test('confirmed page replacement resets the quiet window after an ambiguous change', () => {
  const watch = createStallWatch();
  const first = paper();
  const settled = paper([[12, 0, 84, 60, 145]]);
  sampleStallWatch(watch, first, width, height, 0, 'standard');
  let check;
  for (let now = 2_000; now <= 32_000; now += 2_000)
    check = sampleStallWatch(watch, settled, width, height, now, 'standard');
  assert.equal(settleAmbiguousPage(watch, check.version, { page_changed: true }, 40_000), 'page_changed');
  assert.equal(watch.pageVersion, 1);
  assert.equal(watch.observedSince, 40_000);
  assert.equal(sampleStallWatch(watch, settled, width, height, 180_000, 'standard').checkinReady, undefined);
});

test('a pen resting briefly in the target area does not count as written progress', () => {
  const watch = createStallWatch();
  const working = paper([[20, 20, 40, 40, 100]]);
  const pen = paper([
    [20, 20, 40, 40, 100],
    [32, 22, 37, 40, 30],
  ]);
  seedFocus(watch, working);
  const previousProgressAt = watch.lastProgressAt;
  sampleStallWatch(watch, pen, width, height, 200_000, 'standard');
  sampleStallWatch(watch, pen, width, height, 202_000, 'standard');
  sampleStallWatch(watch, pen, width, height, 204_000, 'standard');
  sampleStallWatch(watch, working, width, height, 206_000, 'standard');
  sampleStallWatch(watch, working, width, height, 208_000, 'standard');
  sampleStallWatch(watch, working, width, height, 210_000, 'standard');
  assert.equal(watch.lastProgressAt, previousProgressAt);
  assert.equal(sampleStallWatch(watch, working, width, height, 302_000, 'standard').ready, true);
});

test('a full page replacement resets the tracked question', () => {
  const watch = createStallWatch();
  const page = paper();
  const replaced = paper([[0, 0, width, height, 145]]);
  sampleStallWatch(watch, page, width, height, 0, 'standard');
  sampleStallWatch(watch, replaced, width, height, 2_000, 'standard');
  sampleStallWatch(watch, replaced, width, height, 4_000, 'standard');
  sampleStallWatch(watch, replaced, width, height, 6_000, 'standard');
  const change = sampleStallWatch(watch, replaced, width, height, 10_000, 'standard');
  assert.equal(change.pageChanged, true);
  assert.equal(watch.pageVersion, 1);
  assert.equal(watch.focus, null);
});

test('repeated activity in a new region eventually shifts the tracked question', () => {
  const watch = createStallWatch();
  const first = paper([[20, 20, 40, 40, 100]]);
  const second = paper([
    [20, 20, 40, 40, 100],
    [65, 20, 80, 40, 100],
  ]);
  seedFocus(watch, first);
  const earlierFocus = watch.focus;
  sampleStallWatch(watch, second, width, height, 20_000, 'standard');
  sampleStallWatch(watch, first, width, height, 25_000, 'standard');
  sampleStallWatch(watch, second, width, height, 30_000, 'standard');
  assert.notDeepEqual(watch.focus, earlierFocus);
  assert.equal(watch.focusSince, 30_000);
});
