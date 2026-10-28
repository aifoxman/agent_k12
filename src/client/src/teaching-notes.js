// A saved board is independent of the current camera frame and its annotations.
export function teachingDraftFromTarget(target, sessionId, taskId = '') {
  if (
    !sessionId ||
    target?.kind !== 'teaching' ||
    !Array.isArray(target.beats) ||
    !target.beats.some((beat) => beat.scratch?.length)
  )
    return null;
  return {
    sessionId,
    taskId: taskId || '',
    box: target.box || null,
    label: target.label || '本次讲解',
    sceneKey: target.sceneKey || target.label || 'current',
    beats: target.beats,
  };
}

export function latestTeachingDraft(hints, sessionId, taskId = '') {
  if (!sessionId) return null;
  const history = (hints || []).filter(
    (hint) => hint.session_id === sessionId && (hint.task_id || '') === (taskId || ''),
  );
  for (const hint of history) {
    const scene = hint.teaching_scene;
    const draft = teachingDraftFromTarget(
      { kind: 'teaching', box: scene?.box, label: scene?.label, beats: scene?.beats },
      sessionId,
      taskId,
    );
    if (draft) return { ...draft, hintId: hint.id, sceneKey: hint.id };
  }
  // Old records contain spoken text only. Preserve that record without inventing
  // missing geometry or pretending it is the original sequence of ink strokes.
  const latest = history.find((hint) => !hint.teaching_scene && hint.response?.trim());
  if (!latest) return null;
  return {
    sessionId,
    taskId: taskId || '',
    hintId: latest.id,
    sceneKey: latest.id,
    box: null,
    label: '上次讲解记录',
    legacy: true,
    beats: [
      {
        speech: latest.response,
        page: [],
        scratch: [{ kind: 'text', role: 'prose', x: 8, y: 16, text: latest.response }],
      },
    ],
  };
}

export function teachingDraftInScope(draft, sessionId, taskId = '') {
  return draft?.sessionId === sessionId && draft.taskId === (taskId || '') ? draft : null;
}
