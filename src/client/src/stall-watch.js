// Camera motion is only a cue to inspect a question. It never proves that a child is stuck.
export const HELP_WAIT = {
  standard: { focusMs: 300_000, idleMs: 180_000 },
  gentle: { focusMs: 480_000, idleMs: 300_000 },
};

export function createStallWatch() {
  return {
    previous: null,
    reference: null,
    observedSince: 0,
    focus: null,
    focusSince: 0,
    lastProgressAt: 0,
    lastMotionAt: 0,
    pendingFocus: null,
    pendingPageAt: 0,
    pendingPageSamples: 0,
    pendingProgressAt: 0,
    pendingMotion: false,
    stableSamples: 0,
    needsReference: false,
    version: 0,
    pageVersion: 0,
    probePending: false,
    deferUntil: 0,
    silenced: false,
    checkinOffered: false,
  };
}

function grayscale(rgba, width, height) {
  const pixels = new Uint8Array(width * height);
  for (let i = 0; i < pixels.length; i++) {
    const offset = i * 4;
    pixels[i] = Math.round((rgba[offset] * 3 + rgba[offset + 1] * 6 + rgba[offset + 2]) / 10);
  }
  return pixels;
}

function difference(current, previous, width, height) {
  let count = 0;
  const tiles = new Uint16Array(48);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x;
      if (Math.abs(current[i] - previous[i]) <= 24) continue;
      count++;
      const column = Math.min(7, Math.floor((x * 8) / width));
      const row = Math.min(5, Math.floor((y * 6) / height));
      tiles[row * 8 + column]++;
    }
  }
  let dominant = 0;
  for (let i = 1; i < tiles.length; i++) if (tiles[i] > tiles[dominant]) dominant = i;
  const tileArea = (width * height) / tiles.length;
  const spread = tiles.reduce((total, value) => total + Number(value > tileArea * 0.14), 0);
  return { fraction: count / (width * height), dominant, spread };
}

function writingAppeared(reference, current, width, height, focus) {
  if (!reference || !focus) return false;
  const left = Math.floor(Math.max(0, focus[0] - 0.08) * width);
  const top = Math.floor(Math.max(0, focus[1] - 0.08) * height);
  const right = Math.ceil(Math.min(1, focus[0] + focus[2] + 0.08) * width);
  const bottom = Math.ceil(Math.min(1, focus[1] + focus[3] + 0.08) * height);
  let darkened = 0;
  let changed = 0;
  for (let y = top; y < bottom; y++) {
    for (let x = left; x < right; x++) {
      const i = y * width + x;
      const delta = reference[i] - current[i];
      if (Math.abs(delta) > 24) changed++;
      if (delta > 35 && reference[i] > 85) darkened++;
    }
  }
  const area = Math.max(1, (right - left) * (bottom - top));
  return darkened >= 3 && changed / area < 0.13;
}

function regionChanged(reference, current, width, height, box) {
  if (!reference || !box) return false;
  const left = Math.floor(box[0] * width);
  const top = Math.floor(box[1] * height);
  const right = Math.ceil((box[0] + box[2]) * width);
  const bottom = Math.ceil((box[1] + box[3]) * height);
  let changed = 0;
  for (let y = top; y < bottom; y++) {
    for (let x = left; x < right; x++) {
      if (Math.abs(reference[y * width + x] - current[y * width + x]) > 24) changed++;
    }
  }
  return changed >= 3;
}

export function sampleStallWatch(watch, rgba, width, height, now, mode) {
  const pixels = grayscale(rgba, width, height);
  if (!watch.previous) {
    watch.previous = pixels;
    watch.reference = pixels;
    watch.observedSince = now;
    return { ready: false };
  }
  const change = difference(pixels, watch.previous, width, height);
  watch.previous = pixels;
  const fromReference = difference(pixels, watch.reference, width, height);
  if (fromReference.fraction > 0.22 && fromReference.spread >= 24) {
    if (!watch.pendingPageAt) watch.pendingPageAt = now;
    watch.pendingPageSamples++;
    watch.stableSamples = change.fraction <= 0.007 ? watch.stableSamples + 1 : 0;
    if (watch.probePending && change.fraction <= 0.007) return { ready: false };
    watch.version++;
    watch.probePending = false;
    watch.pendingMotion = true;
    if (change.fraction > 0.007) watch.lastMotionAt = now;
    // A moving arm or a shifted book can change much of one frame. Only a
    // sustained, whole-page replacement resets the tracked question.
    if (
      fromReference.fraction > 0.32 &&
      fromReference.spread >= 36 &&
      watch.pendingPageSamples >= 4 &&
      now - watch.pendingPageAt >= 8_000
    ) {
      const version = watch.version;
      const pageVersion = watch.pageVersion + 1;
      Object.assign(watch, createStallWatch(), {
        previous: pixels,
        reference: pixels,
        observedSince: now,
        needsReference: true,
        pendingMotion: true,
        version,
        pageVersion,
        lastMotionAt: now,
      });
      return { ready: false, pageChanged: true };
    }
    // A settled image can differ from the first frame across much of the
    // worksheet without meeting the strict page-turn threshold. Ask the
    // server to compare pages instead of waiting in this branch forever.
    if (
      watch.pendingPageSamples >= 15 &&
      now - watch.pendingPageAt >= 30_000 &&
      watch.stableSamples >= 3
    ) {
      watch.probePending = true;
      return { ready: false, ambiguousPageReady: true, version: watch.version };
    }
    return { ready: false };
  }
  watch.pendingPageAt = 0;
  watch.pendingPageSamples = 0;
  if (change.fraction > 0.007) {
    const centerX = ((change.dominant % 8) + 0.5) / 8;
    const centerY = (Math.floor(change.dominant / 8) + 0.5) / 6;
    const focus = [
      Math.min(0.75, Math.max(0, centerX - 0.125)),
      Math.min(0.66, Math.max(0, centerY - 0.17)),
      0.25,
      0.34,
    ];
    const prior = watch.focus;
    const movedToAnotherQuestion =
      !prior ||
      Math.abs(centerX - (prior[0] + prior[2] / 2)) > 0.24 ||
      Math.abs(centerY - (prior[1] + prior[3] / 2)) > 0.22;
    watch.version++;
    watch.probePending = false;
    watch.pendingProgressAt = 0;
    if (movedToAnotherQuestion) {
      const pending = watch.pendingFocus;
      const sameRegion =
        pending &&
        Math.abs(pending.centerX - centerX) < 0.15 &&
        Math.abs(pending.centerY - centerY) < 0.15 &&
        now - pending.firstAt <= 60_000;
      watch.pendingFocus = sameRegion
        ? { ...pending, count: pending.count + 1, lastAt: now }
        : { centerX, centerY, box: focus, firstAt: now, lastAt: now, count: 1 };
    } else if (!movedToAnotherQuestion) {
      watch.pendingFocus = null;
    }
    const confirmedShift =
      watch.pendingFocus?.count >= 3 && now - watch.pendingFocus.firstAt >= 8_000;
    if (confirmedShift) {
      watch.focus = focus;
      watch.focusSince = now;
      watch.lastProgressAt = now;
      watch.needsReference = true;
      watch.pendingFocus = null;
      watch.deferUntil = 0;
      watch.silenced = false;
    }
    watch.pendingMotion = true;
    watch.stableSamples = 0;
    watch.lastMotionAt = now;
    return { ready: false };
  }
  if (watch.pendingMotion && ++watch.stableSamples >= 2) {
    watch.pendingMotion = false;
    if (
      watch.pendingFocus &&
      !regionChanged(watch.reference, pixels, width, height, watch.pendingFocus.box)
    )
      watch.pendingFocus = null;
    if (watch.needsReference) {
      watch.reference = pixels;
      watch.needsReference = false;
      return { ready: false, referenceRefresh: true };
    }
    if (writingAppeared(watch.reference, pixels, width, height, watch.focus)) {
      watch.pendingProgressAt = now;
      return { ready: false };
    }
  }
  if (watch.pendingProgressAt) {
    if (!writingAppeared(watch.reference, pixels, width, height, watch.focus)) {
      watch.pendingProgressAt = 0;
    } else if (now - watch.pendingProgressAt >= 10_000) {
      watch.lastProgressAt = now;
      watch.reference = pixels;
      watch.needsReference = true;
      watch.pendingProgressAt = 0;
      watch.silenced = false;
      watch.probePending = false;
      watch.deferUntil = 0;
      watch.version++;
      return { ready: false, progress: true, referenceRefresh: true };
    }
  }
  const wait = HELP_WAIT[mode];
  if (
    wait &&
    !watch.focus &&
    !watch.checkinOffered &&
    !watch.probePending &&
    !watch.pendingMotion &&
    now >= watch.deferUntil &&
    now - watch.observedSince >= wait.idleMs &&
    now - watch.lastMotionAt >= 20_000
  ) {
    watch.probePending = true;
    return { ready: false, checkinReady: true, version: watch.version };
  }
  if (
    !wait ||
    !watch.focus ||
    watch.pendingFocus ||
    watch.pendingProgressAt ||
    watch.silenced ||
    watch.probePending ||
    now < watch.deferUntil ||
    now - watch.focusSince < wait.focusMs ||
    now - watch.lastProgressAt < wait.idleMs ||
    now - watch.lastMotionAt < 20_000
  )
    return { ready: false };
  watch.probePending = true;
  return {
    ready: true,
    version: watch.version,
    activityBox: watch.focus,
    focusElapsedMs: now - watch.focusSince,
  };
}

export function settleAmbiguousPage(watch, version, result, now) {
  if (version !== watch.version) return 'stale';
  const pixels = watch.previous;
  const pageChanged = Boolean(result?.page_changed);
  const observedSince = result?.offer ? watch.observedSince : now;
  Object.assign(watch, createStallWatch(), {
    previous: pixels,
    reference: pixels,
    observedSince,
    lastMotionAt: now,
    pageVersion: watch.pageVersion + Number(pageChanged),
    version: version + 1,
  });
  return pageChanged ? 'page_changed' : 'rebaseline';
}

export function settleIdleCheckin(watch, version, result, now) {
  if (version !== watch.version) return 'stale';
  watch.probePending = false;
  if (result?.page_changed || result?.progress) {
    const pixels = watch.previous;
    const pageVersion = watch.pageVersion + Number(Boolean(result.page_changed));
    Object.assign(watch, createStallWatch(), {
      previous: pixels,
      reference: pixels,
      observedSince: now,
      pageVersion,
    });
    return 'rebaseline';
  }
  if (result?.offer) {
    watch.checkinOffered = true;
    return 'offer';
  }
  watch.deferUntil = now + 60_000;
  return 'deferred';
}

export function settleStallProbe(watch, version, result, mode, now) {
  if (version !== watch.version) return 'stale';
  watch.probePending = false;
  if (!result?.found) {
    watch.silenced = true;
    return 'silent';
  }
  const extra = result.effort === 'extended' ? 180_000 : 0;
  const earliest = watch.focusSince + HELP_WAIT[mode].focusMs + extra;
  if (now < earliest) {
    watch.deferUntil = earliest;
    return 'deferred';
  }
  watch.silenced = true;
  return 'offer';
}
