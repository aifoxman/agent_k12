// One cancellable generation owns every callback in a teaching request.
export function createHintContext() {
  let generation = 0;
  let controller = null;
  return {
    invalidate() {
      generation++;
      controller?.abort();
      controller = null;
    },
    begin() {
      this.invalidate();
      const version = generation;
      const request = new AbortController();
      controller = request;
      return {
        controller: request,
        signal: request.signal,
        current: () => generation === version && !request.signal.aborted,
      };
    },
  };
}

export function nextHintLevel(currentLevel, sameQuestion, advance = false) {
  if (!sameQuestion) return 1;
  return Math.max(1, Math.min(2, currentLevel + (advance ? 1 : 0)));
}

export function finishConfirmationText(pendingTasks, capturedPages) {
  return [
    '要结束本次作业吗？',
    pendingTasks ? `还有${pendingTasks}项任务未提交，会继续留在清单中。` : '',
    capturedPages ? `已拍的${capturedPages}页尚未提交，结束后需要重新拍摄。` : '',
    '确认结束作业，或取消继续。',
  ].join('');
}
