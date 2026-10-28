import { teachingDraftFromTarget, teachingDraftInScope } from './teaching-notes.js';

// Localization and speech arrive independently. A later target/done event may
// refine the paper location, but must retain every already-spoken ink step.
export function mergeTeachingTarget(current, update) {
  const previous = current?.imageUrl === update.imageUrl ? current : {};
  return {
    ...previous,
    kind: 'teaching',
    sceneKey: update.sceneKey || previous.sceneKey,
    imageUrl: update.imageUrl,
    box: update.box || previous.box || null,
    label: update.label || previous.label || '当前题目',
    cue: update.cue ?? previous.cue ?? null,
    beats: update.beats || previous.beats || [],
    textAnchors: update.textAnchors || previous.textAnchors || [],
  };
}

// Saving a lesson never opens it. Only an active lesson or an explicit review
// may put its thinking board on screen.
export function visibleTeachingDraft({
  mode,
  stage,
  target,
  savedDraft,
  reviewDraft = null,
  sessionId,
  taskId = '',
}) {
  if (mode === 'lesson' && stage === 'active')
    return teachingDraftFromTarget(target, sessionId, taskId);
  if (mode === 'review' && ['active', 'paused'].includes(stage))
    return teachingDraftInScope(reviewDraft === null ? savedDraft : reviewDraft, sessionId, taskId);
  return null;
}

export const LESSON_QUESTION =
  '请完整讲解当前题目的思路和解题步骤，在原题上指出条件、边讲边标记，解释每一步为什么这样做，并说明怎样检查。思考理由写在草稿，具体解题和画线尽量在原题上。';

export function teachingFollowupPrompt(result = {}) {
  const beats = Array.isArray(result.visual_beats) ? result.visual_beats : [];
  const last = beats.at(-1);
  if (!last?.speech) return result.response || '';
  let first = beats.length - 1;
  if (Number.isInteger(last.step_id))
    while (first > 0 && beats[first - 1].step_id === last.step_id) first--;
  // Keep the current question and its immediate context visible. The complete
  // explanation remains in the archive rather than replacing this reply cue.
  return beats
    .slice(first)
    .map((beat) => beat.speech || '')
    .join('');
}
