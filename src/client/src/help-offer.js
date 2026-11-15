export const HELP_REPLY_WAIT_MS = 20_000;

// Separate silence from recognition failures. No microphone permission is
// required for the child to use buttons or for a quiet offer to continue.
export function createHelpOfferDeadline({
  onTimeout,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  now = Date.now,
}) {
  let timer = null;
  let generation = 0;
  let deadline = 0;
  const cancel = () => {
    generation++;
    clearTimer(timer);
    timer = null;
    deadline = 0;
  };
  const resume = (context) => {
    if (context !== 'help_offer' || !deadline) return;
    const current = generation;
    clearTimer(timer);
    timer = setTimer(
      () => {
        if (current !== generation) return;
        cancel();
        onTimeout(context);
      },
      Math.max(0, deadline - now()),
    );
  };
  return {
    cancel,
    hold() {
      generation++;
      clearTimer(timer);
      timer = null;
    },
    resume,
    arm(context) {
      cancel();
      if (context !== 'help_offer') return;
      deadline = now() + HELP_REPLY_WAIT_MS;
      resume(context);
    },
  };
}

export function helpOfferCanStart(offer, scope) {
  return Boolean(
    offer &&
    offer.sessionId === scope.sessionId &&
    offer.frameRevision === scope.frameRevision &&
    scope.stage === 'active' &&
    scope.proactive &&
    !scope.busy &&
    !scope.hidden,
  );
}
