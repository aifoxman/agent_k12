// Guidance depth belongs to a question, independently of the lesson's steps.
export const READING_QUESTION =
  '请读清当前题目，在原题上圈划重要条件和问题，说明题意与一种实质解题思路，让我能够自己开始写；先不直接给最终答案，也不用连续追问。';
export const THINKING_QUESTION =
  '请完整讲解这道题的解题思考、具体步骤、结果和检查；在原题上圈划条件与展示操作，思考和比较写在草稿，不用连续追问。';

export function teachingTier(value) {
  return Number(value) >= 2 ? 2 : 1;
}

export function teachingExplanation(result = {}) {
  const states = {
    ready: { title: '', message: '', retry: false },
    needs_clarification: {
      title: '这题还有内容需要核对',
      message: '这题还有内容需要核对，可重试这层讲解。',
      retry: true,
    },
    needs_parent: {
      title: '请家长一起核对',
      message: '这题需要家长一起核对，核对后再继续。',
      retry: false,
    },
    interrupted: {
      title: '讲解未完整收到',
      message: '本层讲解未完整收到，可以重试本层。',
      retry: true,
    },
  };
  const beats = Array.isArray(result.visual_beats)
    ? result.visual_beats
    : Array.isArray(result.beats)
      ? result.beats
      : [];
  // Saved scenes omit the server status, so only their last meaningful beat
  // decides whether an earlier question was resolved or the lesson still waits.
  let lastBeat;
  for (let index = beats.length - 1; index >= 0; index -= 1) {
    const beat = beats[index];
    if (
      beat &&
      typeof beat === 'object' &&
      !Array.isArray(beat) &&
      (beat.needs_parent === true ||
        (typeof beat.teaching_move === 'string' && beat.teaching_move.trim()) ||
        (typeof beat.speech === 'string' && beat.speech.trim()))
    ) {
      lastBeat = beat;
      break;
    }
  }
  const savedStatus =
    lastBeat?.needs_parent === true ||
    ['parent_handoff', 'parent'].includes(lastBeat?.teaching_move)
      ? 'needs_parent'
      : lastBeat?.teaching_move === 'clarify_target'
        ? 'needs_clarification'
        : 'ready';
  const status =
    result.delivery_complete === false
      ? 'interrupted'
      : result.explanation_status == null
        ? savedStatus
        : Object.hasOwn(states, result.explanation_status)
          ? result.explanation_status
          : 'interrupted';
  return { status, ready: status === 'ready', ...states[status] };
}

export function asksForCompleteTeaching(value) {
  const text = String(value || '');
  if (/不(?:要|用)(?:完整|全部)(?:讲|解)/u.test(text)) return false;
  return /解题思考|完整(?:地)?(?:讲|解|示范)|全部(?:讲|解)|详细(?:讲|解)|(?:告诉我|给我|直接说)(?:这题的?)?答案|解题全过程/u.test(
    text,
  );
}

export function guidanceInScope(binding, { sessionId, taskId = '', frameRevision }) {
  return Boolean(
    binding?.image &&
    binding.hintId &&
    binding.box &&
    binding.sessionId === sessionId &&
    binding.taskId === taskId &&
    binding.frameRevision === frameRevision,
  );
}

export function guidanceRequest({
  binding = null,
  sessionId,
  taskId = '',
  frameRevision,
  prompt = '',
  reference = '',
  newSelection = false,
  candidateId = '',
  advance = false,
  studentThought = '',
}) {
  if (candidateId) return { level: 1, binding: null, replay: false };
  const sameQuestion = Boolean(
    guidanceInScope(binding, { sessionId, taskId, frameRevision }) &&
    !newSelection &&
    !candidateId &&
    !/换(?:一)?题|另(?:一|道)(?:题)?|新题|下一题/u.test(prompt) &&
    (!reference || reference === binding.reference),
  );
  const level =
    advance || asksForCompleteTeaching(prompt) ? 2 : sameQuestion ? teachingTier(binding.level) : 1;
  return {
    level,
    binding: sameQuestion ? binding : null,
    replay: sameQuestion && advance && teachingTier(binding.level) === 2 && !studentThought.trim(),
  };
}
