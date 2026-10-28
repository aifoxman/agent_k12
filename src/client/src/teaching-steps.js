const DEFAULT_MAX_CHARS = 160;

function comparableText(value) {
  return String(value || '')
    .normalize('NFKC')
    .toLowerCase()
    .replaceAll('除以', '÷')
    .replaceAll('乘以', '×')
    .replaceAll('等于', '=')
    .replace(/[^\p{Letter}\p{Number}+=×÷*/²³.−-]/gu, '');
}

function matchingOffset(speech, text) {
  const haystack = comparableText(speech);
  const needle = comparableText(text);
  if (!needle) return -1;
  let offset = haystack.indexOf(needle);
  while (offset >= 0) {
    // A circle for 12 must not match 112, and A must not match the word area.
    if (
      !/^[a-z\d.]+$/u.test(needle) ||
      (!/[a-z\d.]/u.test(haystack[offset - 1] || '') &&
        !/[a-z\d.]/u.test(haystack[offset + needle.length] || ''))
    )
      return offset;
    offset = haystack.indexOf(needle, offset + 1);
  }
  return -1;
}

export function splitTeachingSpeech(speech, maxChars = DEFAULT_MAX_CHARS) {
  const remaining = [...String(speech || '')];
  const limit = Math.max(12, Math.floor(maxChars) || DEFAULT_MAX_CHARS);
  const parts = [];
  while (remaining.length > limit) {
    let boundary = 0;
    // Sentence boundaries are clearer than a full box of text; retain all
    // punctuation and whitespace so concatenating the pieces is lossless.
    for (const pattern of [/[。！？；!?;]/u, /[，、,：:\s]/u]) {
      for (let index = limit - 1; index >= 7; index--)
        if (pattern.test(remaining[index])) {
          boundary = index + 1;
          break;
        }
      if (boundary) break;
    }
    if (!boundary) {
      boundary = limit;
      // Keep numbers, Latin labels, decimal measurements and formulas intact.
      const atomic = (letter) =>
        Boolean(letter) && /^[a-zA-Z\d.+=×÷*/²³−-]+$/u.test(letter.normalize('NFKC'));
      while (boundary > 0 && atomic(remaining[boundary - 1]) && atomic(remaining[boundary]))
        boundary--;
      if (!boundary) {
        boundary = limit;
        while (boundary < remaining.length && atomic(remaining[boundary])) boundary++;
      }
    }
    parts.push(remaining.splice(0, boundary).join(''));
  }
  if (remaining.length || !parts.length) parts.push(remaining.join(''));
  return parts;
}

export function splitTeachingBeat(
  beat,
  { sourceBeatIndex = 0, maxChars = DEFAULT_MAX_CHARS } = {},
) {
  const sentences = String(beat.speech || '').match(/[^。！？!?]*[。！？!?]+|[^。！？!?]+$/gu) || [
    '',
  ];
  const cues = (beat.page || []).flatMap((mark) => [mark.anchor_text, mark.text]).filter(Boolean);
  const passages = [];
  let passage = '';
  let hasCue = false;
  for (const sentence of sentences) {
    const nextCue = cues.some((text) => matchingOffset(sentence, text) >= 0);
    if (passage && hasCue && nextCue) {
      passages.push(passage);
      passage = '';
      hasCue = false;
    }
    passage += sentence;
    hasCue ||= nextCue;
  }
  if (passage || !passages.length) passages.push(passage);
  const speech = passages.flatMap((part) => splitTeachingSpeech(part, maxChars));
  const last = speech.length - 1;
  const steps = speech.map((part, substepIndex) => ({
    ...beat,
    id: `${beat.id ?? 'beat'}:${sourceBeatIndex}:${substepIndex}`,
    sourceBeatId: beat.id ?? null,
    sourceBeatIndex,
    substepIndex,
    substepCount: speech.length,
    isLastInBeat: substepIndex === last,
    step_id: beat.step_id ?? sourceBeatIndex + 1,
    step_title: beat.step_title || '',
    step_end: substepIndex === last ? beat.step_end !== false : false,
    speech: part,
    page: [],
    scratch: [],
    page_cues: [],
  }));
  const findTextStep = (text) => speech.findIndex((part) => matchingOffset(part, text) >= 0);
  for (const [sourceMarkIndex, mark] of (beat.page || []).entries()) {
    // The actual spoken cue can use Chinese numbers while the note uses a
    // written calculation. Prefer that cue even if the note text occurs earlier.
    const matchedText = [mark.anchor_text, mark.text].find((text) => findTextStep(text) >= 0);
    let index = findTextStep(matchedText);
    if (index < 0 && ['line', 'arrow'].includes(mark.kind))
      index = speech.findIndex((part) => /看图|原图|边|连|画/u.test(part));
    if (index < 0) index = last;
    const offset = matchingOffset(speech[index], matchedText);
    steps[index].page_cues.push({
      markIndex: steps[index].page.length,
      sourceMarkIndex,
      charOffset: offset < 0 ? 0 : offset,
      fraction: offset < 0 ? 0 : offset / Math.max(1, comparableText(speech[index]).length),
    });
    steps[index].page.push(mark);
  }
  const diagramStep = speech.findIndex((part) => /示意|画|如图|看这个|看这幅|分成/u.test(part));
  for (const element of beat.scratch || []) {
    // A diagram and its labels remain one coherent picture. Explanatory prose
    // can follow an exact spoken phrase; an unmatched result waits until last.
    const match =
      element.kind === 'text' && element.role === 'prose' ? findTextStep(element.text) : -1;
    const index =
      match >= 0 ? match : element.role !== 'prose' && diagramStep >= 0 ? diagramStep : last;
    steps[index].scratch.push(element);
  }
  return steps;
}

export function teachingSteps(beats = [], options = {}) {
  return beats.flatMap((beat, sourceBeatIndex) =>
    splitTeachingBeat(beat, { ...options, sourceBeatIndex }),
  );
}
