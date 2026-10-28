export function teachingStepKey(beat, index) {
  if (Number.isInteger(beat.step_id)) return `step:${beat.step_id}`;
  if (Number.isInteger(beat.sourceBeatIndex)) return `source:${beat.sourceBeatIndex}`;
  return `beat:${index}`;
}

// One progress scale serves live clips, saved lessons and silent review.
export function logicalTeachingSteps(beats = []) {
  const groups = [];
  for (const [index, beat] of beats.entries()) {
    const key = teachingStepKey(beat, index);
    const previous = groups.at(-1);
    if (previous?.key === key) previous.end = index;
    else
      groups.push({
        key,
        start: index,
        end: index,
        stepId: beat.step_id ?? null,
        title: beat.step_title || '',
      });
  }
  return groups;
}

export function teachingStepIndex(groups, beatIndex = null) {
  if (!groups.length) return 0;
  if (!Number.isFinite(beatIndex)) return groups.length - 1;
  return Math.max(
    0,
    groups.findLastIndex((group) => group.start <= beatIndex),
  );
}
