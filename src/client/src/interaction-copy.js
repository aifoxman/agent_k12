const replyWords = {
  setup_camera: ['检查纸面'],
  timer_subject: ['切换', '保持原科目'],
  help_offer: ['要提示', '不用'],
  correction_ready: ['开始批改'],
  batch: ['确认批改', '拍完了', '重试'],
  batch_confirm: ['确认批改', '重新拍摄'],
  finish_confirm: ['确认结束作业'],
  parent_correction_request: ['允许批改'],
  parent_correction_confirm: ['确认允许批改'],
};

export function choicesForPrompt(context, spoken) {
  return (replyWords[context] || []).filter(
    (word) => spoken.includes(word) || (context === 'batch' && word === '重试'),
  );
}

export function markedSpeechParts(text, choices) {
  if (!text || !choices?.length) return [{ text, highlighted: false }];
  const words = [...choices].sort((a, b) => b.length - a.length);
  const parts = [];
  const shown = new Set();
  let index = 0;
  while (index < text.length) {
    const match = words.find((word) => text.startsWith(word, index));
    if (match) {
      if (shown.has(match)) {
        const last = parts.at(-1);
        if (last && !last.highlighted) last.text += match;
        else parts.push({ text: match, highlighted: false });
      } else {
        parts.push({ text: match, highlighted: true });
        shown.add(match);
      }
      index += match.length;
    } else {
      const last = parts.at(-1);
      if (last && !last.highlighted) last.text += text[index];
      else parts.push({ text: text[index], highlighted: false });
      index++;
    }
  }
  return parts;
}
