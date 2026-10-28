export function shouldProbeHintAttempt(snapshot, now, interactionActive = false) {
  return Boolean(
    snapshot &&
    !snapshot.observed &&
    snapshot.probeCount < 3 &&
    now - snapshot.createdAt < 10 * 60_000 &&
    now - snapshot.lastProbeAt >= 60_000 &&
    !interactionActive,
  );
}

export function needsSpokenReply(message) {
  return /[？?]\s*$/.test(message?.trim() || '');
}
