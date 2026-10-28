function normalizedSpeech(text) {
  return String(text || '')
    .toLocaleLowerCase()
    .replace(
      /[零一二两三四五六七八九]/gu,
      (digit) =>
        ({
          零: '0',
          一: '1',
          二: '2',
          两: '2',
          三: '3',
          四: '4',
          五: '5',
          六: '6',
          七: '7',
          八: '8',
          九: '9',
        })[digit],
    )
    .replace(/[^\p{L}\p{N}]/gu, '');
}

export function likelyPromptEcho(transcript, prompt, heardDuringPlayback) {
  if (!heardDuringPlayback) return false;
  const heard = normalizedSpeech(transcript);
  const spoken = normalizedSpeech(prompt);
  if (!heard || !spoken) return false;
  if (spoken.includes(heard)) return true;
  // ASR often drops a particle or changes one word in a long spoken prompt.
  // Match a close span of the prompt, but keep short action words exact.
  if (heard.length < 8 || heard.length > spoken.length + 2) return false;
  const limit = Math.min(4, Math.ceil(heard.length * 0.22));
  let previous = Array.from({ length: spoken.length + 1 }, () => 0);
  for (let i = 1; i <= heard.length; i++) {
    const current = [i];
    for (let j = 1; j <= spoken.length; j++) {
      current[j] = Math.min(
        previous[j] + 1,
        current[j - 1] + 1,
        previous[j - 1] + (heard[i - 1] === spoken[j - 1] ? 0 : 1),
      );
    }
    previous = current;
  }
  return Math.min(...previous) <= limit;
}
