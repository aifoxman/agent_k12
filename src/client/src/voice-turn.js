export function createReplyPause(pauseMs, now = Date.now) {
  let transcript = '';
  let deadline = 0;
  return {
    get transcript() {
      return transcript;
    },
    get deadline() {
      return deadline;
    },
    noteTranscript(value) {
      const next = String(value || '').trim();
      if (!next || next === transcript) return false;
      transcript = next;
      deadline = now() + pauseMs;
      return true;
    },
    ensureDeadline() {
      if (transcript && !deadline) deadline = now() + pauseMs;
      return deadline;
    },
    clear() {
      deadline = 0;
    },
  };
}

export function completedVoiceReply({ text = '', partial = '', error = '' }) {
  return String(text || (error ? partial : '')).trim();
}
