// Timings contain no photograph, question text, voice text or credentials.
export function createTeachingTrace({
  startedAt,
  now = () => performance.now(),
  emit = () => {},
} = {}) {
  const start = startedAt ?? now(),
    seen = new Set();
  return {
    mark(stage, details = {}) {
      if (seen.has(stage)) return;
      seen.add(stage);
      emit({ stage, elapsed_ms: Math.max(0, Math.round(now() - start)), ...details });
    },
  };
}
