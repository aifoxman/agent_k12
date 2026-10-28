import { logicalTeachingSteps } from './teaching-progress.js';

const STROKE_MS = 900;
const STAGGER_MS = 120;

function finiteNumbers(values) {
  return values.every((value) => typeof value === 'number' && Number.isFinite(value));
}

export function markBounds(mark) {
  if (
    ['line', 'arrow'].includes(mark.kind) &&
    finiteNumbers([mark.x1, mark.y1, mark.x2, mark.y2])
  ) {
    return [
      Math.min(mark.x1, mark.x2),
      Math.min(mark.y1, mark.y2),
      Math.abs(mark.x2 - mark.x1),
      Math.abs(mark.y2 - mark.y1),
    ];
  }
  return Array.isArray(mark.box) && mark.box.length === 4 && finiteNumbers(mark.box)
    ? mark.box
    : null;
}

function teachingPaperRegion(box, marks = [], fullPage = false) {
  const bounds = [box, ...marks.map(markBounds)].filter(
    (value) =>
      Array.isArray(value) &&
      value.length === 4 &&
      finiteNumbers(value) &&
      value[2] >= 0 &&
      value[3] >= 0,
  );
  let region = [0, 0, 1, 1];
  if (!fullPage && bounds.length) {
    // Include neighbouring question text rather than zooming tightly onto a
    // single numeral or geometric point. The full page is always one tap away.
    const left = Math.max(0, Math.min(...bounds.map((b) => b[0])) - 0.1);
    const top = Math.max(0, Math.min(...bounds.map((b) => b[1])) - 0.12);
    const right = Math.min(1, Math.max(...bounds.map((b) => b[0] + b[2])) + 0.1);
    const bottom = Math.min(1, Math.max(...bounds.map((b) => b[1] + b[3])) + 0.12);
    region = [left, top, Math.max(0.25, right - left), Math.max(0.22, bottom - top)];
    region[2] = Math.min(1 - region[0], region[2]);
    region[3] = Math.min(1 - region[1], region[3]);
  }
  return region;
}

export function teachingPaperLayout({ aspect = 4 / 3, box, fullPage = false } = {}) {
  if (
    fullPage ||
    !Number.isFinite(aspect) ||
    aspect <= 0 ||
    !Array.isArray(box) ||
    box.length !== 4 ||
    !finiteNumbers(box) ||
    box[2] <= 0 ||
    box[3] <= 0
  )
    return 'page';
  // Classify the frozen question, not a later annotation, so new ink cannot
  // repeatedly move the paper and board while the teacher is speaking.
  const [, , width, height] = teachingPaperRegion(box);
  return (aspect * width) / height >= 1.6 ? 'wide-question' : 'page';
}

export function teachingPaperFrame({
  width,
  height,
  aspect = 4 / 3,
  box,
  marks = [],
  fullPage = false,
  toolbarInset = 0,
}) {
  const region = teachingPaperRegion(box, marks, fullPage);
  const [x, y, w, h] = region;
  const inset = Math.min(Math.max(0, toolbarInset), Math.max(0, height - 1));
  const availableHeight = Math.max(1, height - inset);
  const imageWidth = Math.min(width / w, (availableHeight * aspect) / h);
  const imageHeight = imageWidth / aspect;
  const viewportHeight = fullPage ? availableHeight : h * imageHeight;
  return {
    region,
    viewport: { left: 0, top: inset, width, height: viewportHeight },
    width: imageWidth,
    height: imageHeight,
    left: (width - w * imageWidth) / 2 - x * imageWidth,
    // A focused question belongs immediately below its controls. Vertically
    // centering a wide question in a tall page column hides it below empty space.
    top: inset + (fullPage ? (availableHeight - imageHeight) / 2 : 0) - y * imageHeight,
  };
}

// The process panel occupies its own row. Its real summary/content border boxes
// determine the reservation; large text never has to fit an estimated bubble.
export function teachingProcessReserve({ height, summaryHeight, contentHeight, expanded }) {
  return (
    Math.min(
      summaryHeight + 2 + (expanded ? contentHeight : 0),
      Math.max(summaryHeight + 2, Math.min(280, height * 0.4)),
    ) + 20
  );
}

// A process list belongs to one current question. Keep the newest occurrence
// of an identical bare calculation, while retaining units and explanatory text.
export function processOperationNotes(notes) {
  const seen = new Set();
  return [...notes]
    .reverse()
    .filter((note) => {
      const text = typeof note.text === 'string' ? note.text.trim() : '';
      const calculation =
        /^[0-9()+\-−×÷*/=. \t]+$/u.test(text) && text.includes('=') && /[+\-−×÷*/]/u.test(text);
      if (!calculation) return true;
      if (seen.has(text)) return false;
      seen.add(text);
      return true;
    })
    .reverse();
}

function markKey(mark) {
  const geometry = markBounds(mark)
    ?.map((value) => Math.round(value * 1000))
    .join(':');
  return mark.anchor_id || `${mark.anchor_text || ''}:${geometry}`;
}

// Two locations stay prominent; earlier evidence remains available on the
// original page. A reviewed step must never include annotations from its future.
export function currentPageMarks(beats, limit = 2, beatIndex = null) {
  const through = Number.isFinite(beatIndex)
    ? Math.max(-1, Math.min(beats.length - 1, Math.floor(beatIndex)))
    : beats.length - 1;
  const marks = beats
    .slice(0, through + 1)
    .flatMap((beat, beatIndex) =>
      (beat.page || []).map((mark, localIndex) => ({ ...mark, beatIndex, localIndex })),
    )
    .filter((mark) => markBounds(mark));
  const anchors = [];
  for (const mark of [...marks].reverse()) {
    const key = markKey(mark);
    if (!anchors.includes(key)) anchors.push(key);
    if (anchors.length >= limit) break;
  }
  const seen = new Set();
  const kept = [...marks]
    .reverse()
    .filter((mark) => {
      // Several operations may refer to the same printed condition. Keep each
      // distinct calculation; repeating the same operation updates its ink.
      const operation =
        mark.kind === 'note'
          ? `:${String(mark.text || '')
              .normalize('NFKC')
              .replace(/\s/gu, '')}`
          : '';
      const key = `${markKey(mark)}:${mark.kind}${operation}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .reverse();
  const indices = new Map();
  return kept.map((mark) => {
    const animationIndex = indices.get(mark.beatIndex) || 0;
    indices.set(mark.beatIndex, animationIndex + 1);
    return { ...mark, animationIndex, emphasized: anchors.includes(markKey(mark)) };
  });
}

export function teachingElementDelay(index) {
  return Math.max(0, index) * STAGGER_MS;
}

export function teachingBeatDuration(beat) {
  const count = Math.max(currentPageMarks([beat]).length, (beat.scratch || []).length);
  return count ? STROKE_MS + teachingElementDelay(count - 1) : 0;
}

function arrowPath(x1, y1, x2, y2, head = 10) {
  const angle = Math.atan2(y2 - y1, x2 - x1);
  return `M ${x1} ${y1} L ${x2} ${y2} M ${x2 - head * Math.cos(angle - 0.55)} ${y2 - head * Math.sin(angle - 0.55)} L ${x2} ${y2} L ${x2 - head * Math.cos(angle + 0.55)} ${y2 - head * Math.sin(angle + 0.55)}`;
}

export function pageMarkPath(mark, width, height) {
  if (mark.kind === 'note') return '';
  const bounds = markBounds(mark);
  if (!bounds) return '';
  const [x, y, w, h] = bounds.map((value, index) => value * (index % 2 ? height : width));
  if (mark.kind === 'line' || mark.kind === 'arrow') {
    const endpoints = finiteNumbers([mark.x1, mark.y1, mark.x2, mark.y2]);
    const x1 = endpoints ? mark.x1 * width : x;
    const y1 = endpoints ? mark.y1 * height : y;
    const x2 = endpoints ? mark.x2 * width : x + w;
    const y2 = endpoints ? mark.y2 * height : y + h;
    return mark.kind === 'arrow' ? arrowPath(x1, y1, x2, y2) : `M ${x1} ${y1} L ${x2} ${y2}`;
  }
  if (mark.kind === 'underline')
    return `M ${x} ${Math.min(height - 2, y + h + 2)} Q ${x + w / 2} ${Math.min(height - 1, y + h + 3)}, ${x + w} ${Math.min(height - 2, y + h + 2)}`;
  if (mark.kind === 'check') {
    const left = Math.min(width - 18, Math.max(2, x >= 22 ? x - 20 : x + w + 4));
    const top = Math.min(height - 18, Math.max(2, y + h / 2 - 8));
    return `M ${left} ${top} h 16 v 16 h -16 Z M ${left + 3} ${top + 8} l 4 4 l 7 -9`;
  }
  const left = Math.max(2, x - 3);
  const top = Math.max(2, y - 3);
  const right = Math.min(width - 2, x + w + 3);
  const bottom = Math.min(height - 2, y + h + 3);
  if (mark.kind === 'box') return `M ${left} ${top} H ${right} V ${bottom} H ${left} Z`;
  const midY = (top + bottom) / 2;
  const radiusX = Math.max(1, (right - left) / 2);
  const radiusY = Math.max(1, (bottom - top) / 2);
  return `M ${left} ${midY} A ${radiusX} ${radiusY} 0 1 1 ${right} ${midY} A ${radiusX} ${radiusY} 0 1 1 ${left} ${midY}`;
}

export function scratchPath(element) {
  if (element.kind === 'line') return `M ${element.x1} ${element.y1} L ${element.x2} ${element.y2}`;
  if (element.kind === 'arrow')
    return arrowPath(element.x1, element.y1, element.x2, element.y2, 3.5);
  if (element.kind === 'rect')
    return `M ${element.x} ${element.y} h ${element.w} v ${element.h} h ${-element.w} Z`;
  if (element.kind === 'circle')
    return `M ${element.x + element.r} ${element.y} A ${element.r} ${element.r} 0 1 1 ${element.x - element.r} ${element.y} A ${element.r} ${element.r} 0 1 1 ${element.x + element.r} ${element.y}`;
  return '';
}

function scratchSegments(element) {
  if (element.kind === 'rect') {
    const { x, y, w, h } = element;
    return [
      [x, y, x + w, y],
      [x + w, y, x + w, y + h],
      [x + w, y + h, x, y + h],
      [x, y + h, x, y],
    ];
  }
  if (element.kind === 'circle')
    return Array.from({ length: 32 }, (_, index) => {
      const start = (index * Math.PI) / 16;
      const end = ((index + 1) * Math.PI) / 16;
      return [
        element.x + element.r * Math.cos(start),
        element.y + element.r * Math.sin(start),
        element.x + element.r * Math.cos(end),
        element.y + element.r * Math.sin(end),
      ];
    });
  if (!['line', 'arrow'].includes(element.kind)) return [];
  const { x1, y1, x2, y2 } = element;
  const segments = [[x1, y1, x2, y2]];
  if (element.kind === 'arrow') {
    const angle = Math.atan2(y2 - y1, x2 - x1);
    for (const offset of [-0.55, 0.55])
      segments.push([
        x2,
        y2,
        x2 - 3.5 * Math.cos(angle + offset),
        y2 - 3.5 * Math.sin(angle + offset),
      ]);
  }
  return segments;
}

function segmentCrossesRect([x1, y1, x2, y2], rect, gap = 0.5) {
  let start = 0;
  let end = 1;
  for (const [point, delta, min, max] of [
    [x1, x2 - x1, rect.x - gap, rect.x + rect.width + gap],
    [y1, y2 - y1, rect.y - gap, rect.y + rect.height + gap],
  ]) {
    if (Math.abs(delta) < 1e-9) {
      if (point < min || point > max) return false;
      continue;
    }
    const a = (min - point) / delta;
    const b = (max - point) / delta;
    start = Math.max(start, Math.min(a, b));
    end = Math.min(end, Math.max(a, b));
    if (start > end) return false;
  }
  return true;
}

function scratchLabelLeader(rect, anchor, labels, leaders) {
  const edge = {
    x: Math.max(rect.x, Math.min(rect.x + rect.width, anchor.x)),
    y: Math.max(rect.y, Math.min(rect.y + rect.height, anchor.y)),
  };
  if (Math.hypot(edge.x - anchor.x, edge.y - anchor.y) <= 0.5) return null;
  const center = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
  const candidates = [[edge, anchor]];
  const xs = [
    1,
    99,
    anchor.x,
    ...labels.flatMap((label) => [label.x - 1, label.x + label.width + 1]),
  ];
  const ys = [anchor.y, ...labels.flatMap((label) => [label.y - 1, label.y + label.height + 1])];
  for (const x of xs.filter((value) => value >= 1 && value <= 99)) {
    const start = { x: x < center.x ? rect.x : rect.x + rect.width, y: center.y };
    candidates.push([start, { x, y: start.y }, { x, y: anchor.y }, anchor]);
  }
  for (const y of ys) {
    const start = { x: center.x, y: y < center.y ? rect.y : rect.y + rect.height };
    candidates.push([start, { x: start.x, y }, { x: anchor.x, y }, anchor]);
  }
  const segments = (points) =>
    points.slice(1).map((point, index) => [points[index].x, points[index].y, point.x, point.y]);
  // Prefer a short direct callout. When another caption or its callout blocks
  // it, a separate elbow keeps the endpoint readable instead of merging lines.
  const existing = leaders.flatMap((leader) => segments(leader.points));
  const score = (points) => {
    const path = segments(points);
    const labelCrossings = path.filter((segment) =>
      labels.some((label) => segmentCrossesRect(segment, label, 0.25)),
    ).length;
    const sharedRuns = path.filter(([x1, y1, x2, y2]) =>
      existing.some(([a, b, c, d]) => {
        const length = Math.hypot(x2 - x1, y2 - y1);
        if (!length) return false;
        const distance = (x, y) => Math.abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) / length;
        return (
          distance(a, b) < 0.5 &&
          distance(c, d) < 0.5 &&
          Math.min(Math.max(x1, x2), Math.max(a, c)) >=
            Math.max(Math.min(x1, x2), Math.min(a, c)) &&
          Math.min(Math.max(y1, y2), Math.max(b, d)) >= Math.max(Math.min(y1, y2), Math.min(b, d))
        );
      }),
    ).length;
    return (
      labelCrossings * 10000 +
      sharedRuns * 10000 +
      path.reduce((sum, [x1, y1, x2, y2]) => sum + Math.hypot(x2 - x1, y2 - y1), 0) +
      points.length
    );
  };
  let selected = candidates[0];
  let selectedScore = score(selected);
  if (selectedScore >= 10000)
    for (const candidate of candidates.slice(1)) {
      const candidateScore = score(candidate);
      if (candidateScore < selectedScore) {
        selected = candidate;
        selectedScore = candidateScore;
      }
    }
  const points = selected.filter(
    (point, index, all) =>
      index === 0 || Math.hypot(point.x - all[index - 1].x, point.y - all[index - 1].y) > 0.01,
  );
  return {
    x1: points[0].x,
    y1: points[0].y,
    x2: anchor.x,
    y2: anchor.y,
    points,
    blocked: selectedScore >= 10000,
  };
}

function placeScratchLabels(diagram, fontSize) {
  const strokes = diagram.filter((element) => element.kind !== 'text').flatMap(scratchSegments);
  const frames = diagram.filter((element) => element.kind === 'rect');
  const divisions = diagram.filter((element) => element.kind === 'line');
  const endpoints = diagram
    .filter((element) => element.kind === 'arrow')
    .flatMap((element) => [
      { x: element.x1, y: element.y1 },
      { x: element.x2, y: element.y2 },
    ]);
  const cellAt = (point) => {
    const frame = frames
      .filter(
        (rect) =>
          point.x > rect.x &&
          point.x < rect.x + rect.w &&
          point.y > rect.y &&
          point.y < rect.y + rect.h,
      )
      .sort((a, b) => a.w * a.h - b.w * b.h)[0];
    if (!frame) return null;
    const xs = [frame.x, frame.x + frame.w],
      ys = [frame.y, frame.y + frame.h];
    for (const line of divisions) {
      if (
        line.x1 > frame.x &&
        line.x1 < frame.x + frame.w &&
        Math.abs(line.x1 - line.x2) < 0.01 &&
        Math.min(line.y1, line.y2) <= frame.y + 0.1 &&
        Math.max(line.y1, line.y2) >= frame.y + frame.h - 0.1
      )
        xs.push(line.x1);
      if (
        line.y1 > frame.y &&
        line.y1 < frame.y + frame.h &&
        Math.abs(line.y1 - line.y2) < 0.01 &&
        Math.min(line.x1, line.x2) <= frame.x + 0.1 &&
        Math.max(line.x1, line.x2) >= frame.x + frame.w - 0.1
      )
        ys.push(line.y1);
    }
    const left = Math.max(...xs.filter((x) => x <= point.x));
    const right = Math.min(...xs.filter((x) => x > point.x));
    const top = Math.max(...ys.filter((y) => y <= point.y));
    const bottom = Math.min(...ys.filter((y) => y > point.y));
    return { x: left, y: top, width: right - left, height: bottom - top };
  };
  const obstacles = strokes.map(([x1, y1, x2, y2]) => ({
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  }));
  const labels = [];
  const leaders = [];
  return diagram.map((element) => {
    if (element.kind !== 'text') return element;
    const textWidth =
      Math.max(
        ...element.lines.map((line) =>
          [...line].reduce((sum, letter) => sum + (/[^\x00-\xff]/u.test(letter) ? 1 : 0.62), 0),
        ),
      ) * fontSize;
    const textHeight = fontSize * (1.2 + (element.lines.length - 1) * 1.3);
    const bounds = (x, y) => ({ x, y: y - fontSize, width: textWidth, height: textHeight });
    const clear = (rect, gap = 0.5) =>
      !strokes.some((stroke) => segmentCrossesRect(stroke, rect, gap)) &&
      !leaders.some((leader) =>
        leader.points
          .slice(1)
          .some((point, index) =>
            segmentCrossesRect(
              [leader.points[index].x, leader.points[index].y, point.x, point.y],
              rect,
              0.25,
            ),
          ),
      ) &&
      !labels.some((label) => overlapArea(rect, label) > 0);
    const original = bounds(element.x, element.y);
    const anchor = { x: element.sourceX ?? element.x, y: element.sourceY ?? element.y };
    const cell = cellAt(anchor);
    if (
      !clear(original) &&
      cell &&
      element.lines.length === 1 &&
      textWidth <= cell.width - 0.2 &&
      fontSize * 1.05 <= cell.height - 0.2
    ) {
      const centered = {
        x: cell.x + (cell.width - textWidth) / 2,
        y: cell.y + (cell.height - fontSize * 1.05) / 2,
        width: textWidth,
        height: fontSize * 1.05,
      };
      if (clear(centered, 0.1)) {
        labels.push(centered);
        return {
          ...element,
          x: cell.x + cell.width / 2,
          y: cell.y + cell.height / 2,
          centered: true,
          anchor,
        };
      }
    }
    if (!cell) {
      // A source caption may sit beside an arrow's tail. Keep that explicit
      // endpoint association when making room for the caption, rather than
      // pointing its callout at an arbitrary nearby shape.
      const nearby = endpoints.filter(
        (point) =>
          point.x >= original.x - fontSize / 2 &&
          point.x <= original.x + original.width + fontSize / 2 &&
          Math.abs(point.y - anchor.y) <= fontSize / 2,
      );
      nearby.sort(
        (a, b) =>
          Math.abs(a.y - anchor.y) * 4 +
          Math.abs(a.x - anchor.x) -
          (Math.abs(b.y - anchor.y) * 4 + Math.abs(b.x - anchor.x)),
      );
      if (nearby.length) Object.assign(anchor, nearby[0]);
    }
    let position = original;
    if (!clear(original)) {
      const candidates = [...obstacles, ...labels].flatMap((obstacle) => [
        bounds(element.x, obstacle.y - textHeight + fontSize - 1),
        bounds(element.x, obstacle.y + obstacle.height + fontSize + 1),
        bounds(Math.max(1, obstacle.x - textWidth - 1), element.y),
        bounds(Math.min(99 - textWidth, obstacle.x + obstacle.width + 1), element.y),
      ]);
      // Keep shape coordinates and proportions intact. Only conflicting labels
      // move, using the nearest free position; interior area labels can stay.
      candidates.sort(
        (a, b) =>
          (a.x - original.x) ** 2 +
          (a.y - original.y) ** 2 -
          ((b.x - original.x) ** 2 + (b.y - original.y) ** 2),
      );
      const free = candidates.filter((candidate) => clear(candidate));
      position =
        free.find(
          (candidate) => !scratchLabelLeader(candidate, anchor, labels, leaders)?.blocked,
        ) ||
        free[0] ||
        original;
    }
    const moved =
      Math.hypot(
        (element.sourceX ?? element.x) - position.x,
        (element.sourceY ?? element.y) - position.y - fontSize,
      ) > 0.25;
    const leader = moved ? scratchLabelLeader(position, anchor, labels, leaders) : null;
    labels.push(position);
    if (leader) leaders.push(leader);
    return {
      ...element,
      x: position.x,
      y: position.y + fontSize,
      anchor,
      ...(leader ? { leader } : {}),
    };
  });
}

export function scratchLayout(beats, width = 300) {
  const elements = beats.flatMap((beat, beatIndex) =>
    (beat.scratch || []).map((element, localIndex) => ({
      ...element,
      beatIndex,
      localIndex,
      ...(Number.isInteger(beat.step_id) ? { stepId: beat.step_id } : {}),
    })),
  );
  const hasGeometry = elements.some((element) => element.kind !== 'text');
  const isMeasurement = (text) =>
    /^[-+]?\d+(?:\.\d+)?\s*[A-Za-z\u3400-\u9fff°℃%²³]{1,6}$/u.test(text);
  const prose = elements.filter(
    (element) =>
      element.kind === 'text' &&
      (element.role === 'prose' ||
        (element.role !== 'label' &&
          !isMeasurement(element.text) &&
          (!hasGeometry || [...String(element.text || '')].length > 12))),
  );
  const proseElements = new Set(prose);
  const steps = new Map();
  for (const element of prose) {
    if (!steps.has(element.beatIndex))
      steps.set(element.beatIndex, { beatIndex: element.beatIndex, elements: [] });
    steps.get(element.beatIndex).elements.push(element);
  }
  // Leave a tenth-pixel margin for SVG viewport subpixel quantization.
  const fontSize = Math.max(5.5, 1610 / Math.max(160, width));
  // Scratch drawings are incremental. Keep their geometry, but a new label in
  // the same slot replaces the old label instead of painting both on top.
  const slots = new Set();
  const rawDiagram = [...elements]
    .reverse()
    .filter((element) => {
      if (proseElements.has(element)) return false;
      const slot =
        element.kind === 'text'
          ? `text:${Math.round(element.x * 10)}:${Math.round(element.y * 10)}`
          : scratchPath(element);
      if (slots.has(slot)) return false;
      slots.add(slot);
      return true;
    })
    .reverse()
    .map((element) => {
      if (element.kind !== 'text') return element;
      const letters = [...String(element.text || '')];
      const shortLabel =
        (element.role === 'label' && letters.length <= 6) || isMeasurement(element.text);
      if (shortLabel) {
        const textWidth =
          letters.reduce((sum, letter) => sum + (/[^\x00-\xff]/u.test(letter) ? 1 : 0.62), 0) *
          fontSize;
        return {
          ...element,
          sourceX: element.x,
          sourceY: element.y,
          x: Math.max(1, Math.min(element.x, 98 - textWidth)),
          lines: [String(element.text)],
        };
      }
      const columns = Math.max(1, Math.floor((100 - element.x) / fontSize));
      const lines = [];
      for (let index = 0; index < letters.length; index += columns)
        lines.push(letters.slice(index, index + columns).join(''));
      return { ...element, sourceX: element.x, sourceY: element.y, lines };
    });
  const diagram = placeScratchLabels(rawDiagram, fontSize);
  // Trim only vertical whitespace. Keeping the 100-unit width preserves label
  // size and every shape's relation to its labels instead of shrinking the ink.
  const verticalBounds = diagram.map((element) => {
    if (element.kind === 'text') {
      const leaderYs = element.leader?.points.map((point) => point.y) || [element.y];
      return [
        Math.min(element.y - fontSize, ...leaderYs),
        Math.max(
          element.y + Math.max(0, element.lines.length - 1) * fontSize * 1.3 + fontSize * 0.35,
          ...leaderYs,
        ),
      ];
    }
    if (element.kind === 'circle') return [element.y - element.r, element.y + element.r];
    if (element.kind === 'rect') return [element.y, element.y + element.h];
    const head = element.kind === 'arrow' ? 3.5 : 0;
    return [Math.min(element.y1, element.y2) - head, Math.max(element.y1, element.y2) + head];
  });
  const top = verticalBounds.length ? Math.min(...verticalBounds.map(([min]) => min)) - 2 : 0;
  const bottom = verticalBounds.length
    ? Math.max(...verticalBounds.map(([, max]) => max)) + 2
    : 100;
  const span = Math.max(1, bottom - top);
  return {
    prose,
    proseSteps: [...steps.values()],
    diagram,
    fontSize,
    viewBox: `0 ${top} 100 ${span}`,
    height: Math.max(
      diagram.length && diagram.every((element) => element.kind === 'text') ? 48 : 180,
      (width * span) / 100,
    ),
  };
}

// Each written step keeps the cumulative drawing as it looked at that point.
// Empty waiting beats keep the same page instead of erasing it or adding one.
export function scratchSnapshots(beats, width = 300) {
  return beats.flatMap((beat, beatIndex) =>
    beat.scratch?.length
      ? [{ beatIndex, content: scratchLayout(beats.slice(0, beatIndex + 1), width) }]
      : [],
  );
}

// The original page owns the operations. Remove only an explicitly duplicated
// source-to-target sketch after its complete operation is already visible there.
// Other figures and every written reason remain untouched in the saved scene.
export function teachingPaperPresentation(beats, { paperVisible = false } = {}) {
  if (!paperVisible) return beats;
  const redundant = new Set();
  const speech = new Map();
  const redirect = (value) => {
    const original = String(value || '');
    const redirected = original.replace(
      /(^|[。！？；])((?:(?:先|再|请)?看(?:一下)?|在)?(?:草稿|草图)[^。！？；：:\n]{0,100})([：:。])/gu,
      (matched, before, instruction, punctuation) => {
        if (
          !/^(?:(?:先|再|请)?看(?:一下)?(?:草稿|草图)(?:(?:中|里)?的)?|(?:草稿|草图)(?:上|中|里)?(?:只)?(?:画|显示|展示))(?:(?:题目|原图|区域|缺口)(?:的)?)?(?:局部)?(?:示意图?|图示)(?:[，,](?:上面表示后面|下面表示前面))*$/u.test(
            instruction,
          )
        )
          return matched;
        return `${before}搬移步骤见原题和解题过程${punctuation}`;
      },
    );
    return /草稿|草图|示意|图示|上图|下图|箭头|小框|方框|上面表示|下面表示/u.test(redirected)
      ? null
      : redirected;
  };
  const text = (value) => String(value || '').replace(/[\s：:，,。；;（）()]/gu, '');
  for (const group of logicalTeachingSteps(beats)) {
    const current = beats.slice(group.start, group.end + 1);
    if (
      current.some((beat) =>
        (beat.scratch || []).some(
          (item) => item.role === 'prose' && /示意|图示|箭头|小框|方框/u.test(item.text || ''),
        ),
      )
    )
      continue;
    const diagram = current
      .flatMap((beat) => beat.scratch || [])
      .filter((item) => item.role !== 'prose');
    if (!diagram.some((item) => item.kind === 'arrow')) continue;
    if (diagram.some((item) => !['text', 'rect', 'arrow'].includes(item.kind))) continue;
    const labels = diagram.filter((item) => item.kind === 'text');
    if (
      labels.some(
        (item) =>
          item.role !== 'label' ||
          !/^(?:(?:来源|目标)\s*[:：].+|(?:搬移|缺口|局部|区域)(?:局部)?示意图?(?:[（(](?:局部|不是整(?:堆)?图)[）)])?|前|后|左|右|上|下|上层|下层|底层|顶层)$/u.test(
            item.text,
          ),
      )
    )
      continue;
    const sources = labels
      .filter((item) => /^来源\s*[:：]/u.test(item.text))
      .map((item) => text(item.text.replace(/^来源\s*[:：]/u, '')));
    const targets = labels
      .filter((item) => /^目标\s*[:：]/u.test(item.text))
      .map((item) => text(item.text.replace(/^目标\s*[:：]/u, '')));
    if (
      !sources.length ||
      !targets.length ||
      [...sources, ...targets].some((value) => value.length < 2)
    )
      continue;
    const marked = new Set(
      beats
        .slice(0, group.end + 1)
        .flatMap((beat) => beat.page || [])
        .filter((mark) => ['circle', 'box'].includes(mark.kind) && mark.anchor_id)
        .map((mark) => mark.anchor_id),
    );
    const notes = current
      .flatMap((beat) => beat.page || [])
      .filter(
        (mark) =>
          mark.kind === 'note' &&
          marked.has(mark.anchor_id) &&
          /搬(?:入|到|至)|移(?:入|到|至)|放(?:入|到|进)|挪(?:入|到|至)/u.test(mark.text || ''),
      )
      .map((mark) => text(mark.text));
    const pairs = sources.flatMap((source) =>
      targets
        .filter((target) => notes.some((note) => note.includes(source) && note.includes(target)))
        .map((target) => ({ source, target })),
    );
    if (
      !sources.every((source) => pairs.some((pair) => pair.source === source)) ||
      !targets.every((target) => pairs.some((pair) => pair.target === target))
    )
      continue;
    const redirected = current.map((beat) => redirect(beat.speech));
    if (redirected.some((value) => value === null)) continue;
    for (let index = group.start; index <= group.end; index++) {
      redundant.add(index);
      speech.set(index, redirected[index - group.start]);
    }
  }
  return redundant.size
    ? beats.map((beat, index) =>
        redundant.has(index)
          ? {
              ...beat,
              ...(typeof beat.speech === 'string' ? { speech: speech.get(index) } : {}),
              scratch: (beat.scratch || []).filter((item) => item.role === 'prose'),
            }
          : beat,
      )
    : beats;
}

export { teachingPaperPresentation as scratchWithPaperOperations };

export function scratchLiveContent(beats, beatIndex, width = 300) {
  const prefix = beats.slice(0, beatIndex + 1);
  const content = scratchLayout(prefix, width);
  if (!content.diagram.length) return content;
  const diagramBeat = Math.max(...content.diagram.map((element) => element.beatIndex));
  const diagramBeats = new Set(scratchDiagramFocusBeats(content, diagramBeat));
  const proseKeys = new Set(
    content.prose.map((element) => `${element.beatIndex}:${element.localIndex}`),
  );
  const latestReason = content.proseSteps.at(-1)?.beatIndex;
  return scratchLayout(
    prefix.map((beat, index) => ({
      ...beat,
      scratch: (beat.scratch || []).filter((_, localIndex) =>
        proseKeys.has(`${index}:${localIndex}`) ? index === latestReason : diagramBeats.has(index),
      ),
    })),
    width,
  );
}

export function scratchPlaybackStep(snapshots, beatIndex = null) {
  if (!snapshots.length || !Number.isFinite(beatIndex)) return null;
  const index = snapshots.findLastIndex((snapshot) => snapshot.beatIndex <= beatIndex);
  const selected = Math.max(0, index);
  return selected === snapshots.length - 1 ? null : selected;
}

export function scratchScrollFocus(content, beatIndex) {
  if (content.diagram.some((element) => element.beatIndex === beatIndex)) return 'diagram';
  if (content.proseSteps.some((step) => step.beatIndex === beatIndex)) return 'prose';
  return content.diagram.length ? 'diagram' : 'end';
}

export function scratchDiagramFocusBeats(content, beatIndex) {
  const current = content.diagram.filter((element) => element.beatIndex === beatIndex);
  if (!current.length) return [];
  const selected = new Set([beatIndex]);
  const strokes = content.diagram.filter(
    (element) => element.kind !== 'text' && element.beatIndex <= beatIndex,
  );
  if (Number.isInteger(current[0].stepId))
    for (const element of content.diagram)
      if (element.beatIndex <= beatIndex && element.stepId === current[0].stepId)
        selected.add(element.beatIndex);
  const pointDistance = (x, y, [x1, y1, x2, y2]) => {
    const dx = x2 - x1,
      dy = y2 - y1,
      length = dx * dx + dy * dy;
    const t = length ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / length)) : 0;
    return Math.hypot(x - x1 - t * dx, y - y1 - t * dy);
  };
  const segmentDistance = (a, b) => {
    const [x1, y1, x2, y2] = a,
      [u1, v1, u2, v2] = b,
      dx = x2 - x1,
      dy = y2 - y1,
      ex = u2 - u1,
      ey = v2 - v1;
    const denominator = dx * ey - dy * ex;
    if (Math.abs(denominator) > 1e-9) {
      const t = ((u1 - x1) * ey - (v1 - y1) * ex) / denominator;
      const u = ((u1 - x1) * dy - (v1 - y1) * dx) / denominator;
      if (t >= 0 && t <= 1 && u >= 0 && u <= 1) return 0;
    }
    return Math.min(
      pointDistance(x1, y1, b),
      pointDistance(x2, y2, b),
      pointDistance(u1, v1, a),
      pointDistance(u2, v2, a),
    );
  };
  const touches = (a, b) => {
    const first = scratchSegments(a),
      second = scratchSegments(b);
    if (
      a.kind === 'rect' &&
      second.some((segment) =>
        segmentCrossesRect(segment, { x: a.x, y: a.y, width: a.w, height: a.h }, 1),
      )
    )
      return true;
    if (
      b.kind === 'rect' &&
      first.some((segment) =>
        segmentCrossesRect(segment, { x: b.x, y: b.y, width: b.w, height: b.h }, 1),
      )
    )
      return true;
    return first.some((segment) => second.some((other) => segmentDistance(segment, other) <= 1));
  };
  // An incremental divider belongs to its earlier enclosing rectangle. Include
  // that beat's labels as well; unrelated older drawings stay outside focus.
  let changed = true;
  while (changed) {
    changed = false;
    const active = strokes.filter((element) => selected.has(element.beatIndex));
    for (const element of strokes) {
      if (selected.has(element.beatIndex)) continue;
      if (active.some((other) => touches(other, element))) {
        selected.add(element.beatIndex);
        changed = true;
      }
    }
  }
  return [...selected].sort((a, b) => a - b);
}

export function overlapArea(a, b) {
  return (
    Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)) *
    Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y))
  );
}

export function placePageLabels(
  marks,
  width,
  height,
  { targetBox = null, visibleBox = null, protectedBoxes = [] } = {},
) {
  const [vx, vy, vw, vh] = visibleBox || [0, 0, 1, 1];
  const viewport = { x: vx * width, y: vy * height, width: vw * width, height: vh * height };
  const fit = (position, labelWidth, labelHeight) => ({
    x: Math.max(viewport.x + 4, Math.min(viewport.x + viewport.width - labelWidth - 4, position.x)),
    y: Math.max(
      viewport.y + 4,
      Math.min(viewport.y + viewport.height - labelHeight - 4, position.y),
    ),
    width: labelWidth,
    height: labelHeight,
  });
  const occupied = marks.map((mark) => {
    const [x, y, w, h] = markBounds(mark);
    return { x: x * width, y: y * height, width: w * width, height: h * height };
  });
  occupied.push(
    ...protectedBoxes
      .filter((box) => Array.isArray(box) && box.length === 4 && finiteNumbers(box))
      .map(([x, y, w, h]) => ({
        x: x * width,
        y: y * height,
        width: w * width,
        height: h * height,
      })),
  );
  return marks.flatMap((mark, index) => {
    if (!['label', 'check', 'note'].includes(mark.kind)) return [];
    const text = mark.kind === 'check' ? '已找到条件' : mark.text;
    if (!text) return [];
    const anchor = occupied[index];
    if (mark.kind === 'note') {
      // A note's box is a verified anchor, never the bubble's chosen position.
      if (!mark.anchor_id || [...text].length > 60) return [];
      const noteWidth = Math.min(
        viewport.width - 16,
        248,
        Math.max(112, [...text].length * 16 + 24),
      );
      const columns = Math.max(1, Math.floor((noteWidth - 24) / 16));
      const noteHeight = 18 + Math.ceil([...text].length / columns) * 22;
      const center = { x: anchor.x + anchor.width / 2, y: anchor.y + anchor.height / 2 };
      const candidates = [
        { x: anchor.x, y: anchor.y - noteHeight - 10 },
        { x: anchor.x, y: anchor.y + anchor.height + 10 },
        { x: anchor.x + anchor.width + 10, y: anchor.y },
        { x: anchor.x - noteWidth - 10, y: anchor.y },
        ...Array.from(
          { length: Math.max(1, Math.ceil(viewport.height / 24)) },
          (_, row) => viewport.y + row * 24 + 8,
        ).flatMap((y) => [
          { x: viewport.x + 8, y },
          { x: viewport.x + viewport.width - noteWidth - 8, y },
        ]),
      ].map((position) => fit(position, noteWidth, noteHeight));
      const obstacles = occupied.map((rect) => ({
        x: rect.x - 5,
        y: rect.y - 5,
        width: rect.width + 10,
        height: rect.height + 10,
      }));
      const question = targetBox && {
        x: targetBox[0] * width,
        y: targetBox[1] * height,
        width: targetBox[2] * width,
        height: targetBox[3] * height,
      };
      const safe = candidates.filter(
        (candidate) =>
          candidate.width > 0 &&
          candidate.height <= viewport.height - 8 &&
          obstacles.every((rect) => overlapArea(candidate, rect) === 0),
      );
      const score = (candidate) =>
        (question ? overlapArea(candidate, question) * 10 : 0) +
        Math.hypot(
          candidate.x + candidate.width / 2 - center.x,
          candidate.y + candidate.height / 2 - center.y,
        );
      safe.sort((a, b) => score(a) - score(b));
      if (!safe.length) return [{ text, mark, index, docked: true }];
      const position = safe[0];
      occupied.push(position);
      return [
        {
          ...position,
          text,
          mark,
          index,
          leader: {
            x1: center.x,
            y1: center.y,
            x2: Math.max(position.x, Math.min(position.x + position.width, center.x)),
            y2: Math.max(position.y, Math.min(position.y + position.height, center.y)),
          },
        },
      ];
    }
    const labelWidth = Math.min(
      viewport.width - 8,
      Math.max(26, [...text].length * 16 + 14 + (mark.kind === 'check' ? 22 : 0)),
    );
    const labelHeight = 27;
    if (mark.kind === 'check') {
      // The underline/circle locates the condition. Its explanatory badge stays
      // in the control margin so a dense previous line is never covered.
      const position = {
        x: Math.max(4, width - labelWidth - 12),
        y: Math.min(128, Math.max(4, height - labelHeight - 4)),
        width: labelWidth,
        height: labelHeight,
      };
      if (visibleBox) {
        const question = targetBox && {
          x: targetBox[0] * width,
          y: targetBox[1] * height,
          width: targetBox[2] * width,
          height: targetBox[3] * height,
        };
        const candidates = [
          { x: viewport.x + viewport.width - labelWidth - 8, y: viewport.y + 8 },
          {
            x: viewport.x + viewport.width - labelWidth - 8,
            y: viewport.y + viewport.height - labelHeight - 8,
          },
          { x: viewport.x + 8, y: viewport.y + viewport.height - labelHeight - 8 },
        ].map((candidate) => fit(candidate, labelWidth, labelHeight));
        const score = (candidate) =>
          occupied.reduce((sum, rect) => sum + overlapArea(candidate, rect), 0) +
          (question ? overlapArea(candidate, question) * 10 : 0);
        candidates.sort((a, b) => score(a) - score(b));
        occupied.push(candidates[0]);
        return [{ ...candidates[0], text, mark, index }];
      }
      occupied.push(position);
      return [{ ...position, text, mark, index }];
    }
    const candidates = [
      { x: anchor.x, y: anchor.y - labelHeight - 5 },
      { x: anchor.x, y: anchor.y + anchor.height + 5 },
      { x: anchor.x + anchor.width + 6, y: anchor.y },
      { x: anchor.x - labelWidth - 6, y: anchor.y },
      ...(mark.purpose === 'move'
        ? occupied.flatMap((rect) => [
            { x: anchor.x, y: rect.y - labelHeight - 5 },
            { x: anchor.x, y: rect.y + rect.height + 5 },
            { x: rect.x + rect.width + 6, y: anchor.y },
            { x: rect.x - labelWidth - 6, y: anchor.y },
          ])
        : []),
    ].map((candidate) => fit(candidate, labelWidth, labelHeight));
    candidates.sort(
      (a, b) =>
        occupied.reduce((sum, rect) => sum + overlapArea(a, rect), 0) -
        occupied.reduce((sum, rect) => sum + overlapArea(b, rect), 0),
    );
    const position = candidates[0];
    if (mark.purpose === 'move' && occupied.some((rect) => overlapArea(position, rect) > 0))
      return [{ text, mark, index, docked: true }];
    occupied.push(position);
    const center = { x: anchor.x + anchor.width / 2, y: anchor.y + anchor.height / 2 };
    return [
      {
        ...position,
        text,
        mark,
        index,
        leader: {
          x1: center.x,
          y1: center.y,
          x2: Math.max(position.x, Math.min(position.x + position.width, center.x)),
          y2: Math.max(position.y, Math.min(position.y + position.height, center.y)),
        },
      },
    ];
  });
}

export function chooseScratchPlacement({
  width,
  height,
  panelWidth,
  panelHeight,
  target,
  avoid = [],
}) {
  const gap = 12;
  const right = Math.max(gap, width - panelWidth - gap);
  const bottom = Math.max(gap, height - panelHeight - gap);
  const yPositions = [
    ...new Set([
      bottom,
      gap,
      ...avoid.flatMap((rect) => [rect.y + rect.height + gap, rect.y - panelHeight - gap]),
    ]),
  ].filter((y) => y >= gap && y <= bottom);
  const candidates = yPositions.flatMap((y) =>
    [right, gap].map((x) => ({ x, y, width: panelWidth, height: panelHeight })),
  );
  const score = (candidate) =>
    (target ? overlapArea(candidate, target) * 4 : 0) +
    avoid.reduce((sum, rect) => sum + overlapArea(candidate, rect), 0);
  candidates.sort((a, b) => score(a) - score(b));
  return { ...candidates[0], docked: score(candidates[0]) > 0 };
}
