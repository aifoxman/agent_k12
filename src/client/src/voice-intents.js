const digits = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };

function spokenNumber(value) {
  if (/^\d+$/.test(value)) return Number(value);
  let total = 0;
  let current = 0;
  for (const char of value.replaceAll('〇', '零')) {
    if (char === '十' || char === '百') {
      total += (current || 1) * (char === '十' ? 10 : 100);
      current = 0;
    } else if (char in digits) current = digits[char];
    else return 0;
  }
  return total + current;
}

export function parseQuestionReference(spoken) {
  const text = String(spoken || '').replace(/[，。！？,.!?\s（）()]/g, '');
  const matches = [
    ...text.matchAll(/第?([0-9]{1,3}|[零〇一二两三四五六七八九十百]{1,5})(大题|小题|题)/g),
  ];
  const parts = matches
    .map((match) => ({ number: spokenNumber(match[1]), kind: match[2] }))
    .filter((part) => part.number > 0 && part.number <= 999);
  const main = parts.filter((part) => part.kind !== '小题');
  if (!parts.length || parts.length !== matches.length || parts.length > 3 || main.length > 1)
    return null;
  if (main.length && parts[0] !== main[0]) return null;
  for (let index = 1; index < matches.length; index++) {
    const previous = matches[index - 1];
    const gap = text.slice(previous.index + previous[0].length, matches[index].index);
    if (!/^的?$/.test(gap) || parts[index].kind !== '小题') return null;
  }
  return parts.map((part) => `第${part.number}${part.kind}`).join('');
}

export function parseLocatedQuestionReference(spoken) {
  const reference = parseQuestionReference(spoken);
  if (!reference) return null;
  const text = String(spoken || '');
  const left = /左页|左侧|左边/.test(text);
  const right = /右页|右侧|右边/.test(text);
  if (left && right) return null;
  return `${left ? '左页' : right ? '右页' : ''}${reference}`;
}

export function isTaskReadCommand(spoken) {
  return /^(读|播报)(一下)?(任务|今天任务|今日任务|作业安排)[。！!]?$/u.test(
    String(spoken || '').trim(),
  );
}

function questionNumber(text) {
  const match = text.match(/第\s*([0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*题/);
  if (!match) return 0;
  const value = match[1];
  if (/^\d+$/.test(value)) return Number(value);
  if (value === '十') return 10;
  if (value.includes('十')) {
    const [tens, ones] = value.split('十');
    return (tens ? digits[tens] : 1) * 10 + (ones ? digits[ones] : 0);
  }
  return digits[value] || 0;
}

export function parseLearningSupport(spoken) {
  const text = String(spoken || '')
    .trim()
    .replace(/[，。！？,.!?\s]+/g, '');
  // Only explicit first-person requests; quoted worksheet stories are not signals.
  if (
    /^(我(现在|真的|有点|已经|实在|也)?(太|很|好)?累了|我想休息(一下|一会儿)?|我需要休息(一下)?|我不想做了|让我休息(一下)?)$/.test(
      text,
    )
  )
    return {
      type: 'offer_rest',
      message: '累了可以先歇一会儿。想休息就点暂停，或者说暂停；也可以按自己的节奏继续。',
    };
  if (
    /^(我(就是|真的|还是|完全)?不会(做|这题)?|我做不出来|我怎么都不会|我不知道怎么开始)$/.test(text)
  )
    return { type: 'clarify_difficulty', message: '我们慢慢来。是题意没读懂，还是卡在某一步？' };
  return null;
}

export function parseVoiceReply(context, spoken) {
  const support = parseLearningSupport(spoken);
  if (support) return support;
  const text = String(spoken || '')
    .trim()
    .replace(/[，。！？,.!?\s]+/g, '');
  if (!text) return null;
  if (
    ['hint_followup', 'hint_attempt_reply'].includes(context) &&
    /^(我自己来|我先自己做)$/.test(text)
  )
    return { type: 'try_independently' };
  if (
    /^(不用|不要|不需要|算了|取消|先不|稍后|不用了|不需要提示|不要提示|我自己来|我先自己做|不允许批改|不同意批改)$/.test(
      text,
    )
  )
    return { type: 'decline' };
  if (
    context === 'setup_camera' &&
    /^(检查[纸字页]面|检查作业|已经摆好|摆好了|准备好了|好了|开始作业|继续作业)$/.test(text)
  )
    return { type: 'check_paper' };
  if (context === 'help_offer') {
    const reference = parseLocatedQuestionReference(text);
    if (reference) return { type: 'hint', question: reference };
    if (/^(要|需要|好|好的|是|可以|要提示|给我提示|帮我)$/.test(text))
      return { type: 'ask_target' };
  }
  if (context === 'hint_target') {
    const reference = parseLocatedQuestionReference(text);
    if (reference) return { type: 'hint', question: reference };
  }
  if (context === 'hint_followup' && /^(解题思考|继续提示|再给一步|下一步|再提示一步)$/.test(text))
    return { type: 'next_hint' };
  if (context === 'hint_followup' || context === 'hint_attempt_reply') {
    if (
      /^(我)?(写好了|做完了|算好了|改好了|这步写好了|帮我看看|看看我写的|检查我写的|帮我检查这一步)$/.test(
        text,
      )
    )
      return { type: 'check_attempt' };
    if (
      /^(我先|我再|让我|先|再)(自己)?(想想|想一下|试试|写写|做做|继续做)(吧)?$/.test(text) ||
      /^(我懂了|我会了|先不提示了)$/.test(text)
    )
      return { type: 'try_independently' };
    if (/^(解题思考|继续提示|再给一步|下一步|再提示一步)$/.test(text)) return { type: 'next_hint' };
  }
  if (context === 'correction_ready') {
    if (/^(批改|批改本页|批改作业|开始批改|检查这页)$/.test(text))
      return { type: 'open_correction' };
  }
  if (context === 'batch') {
    if (/^(确认批改|批改这张|确认提交|提交作业|交给家长)$/.test(text))
      return { type: 'submit_batch' };
    if (/^(拍完了|都拍好了|全部拍完|完成拍照|开始批改|可以批改了|准备提交)$/.test(text))
      return { type: 'review_batch' };
    if (/^(重试|再试一次|重新识别)$/.test(text)) return { type: 'retry_page' };
    if (/^(删除上一张|重拍上一张)$/.test(text)) return { type: 'remove_last_page' };
  }
  if (context === 'batch_confirm') {
    if (/^(确认批改|确认提交批改|确认提交|提交作业)$/.test(text)) return { type: 'submit_batch' };
    if (/^(重新拍摄|重新开始)$/.test(text)) return { type: 'restart_collection' };
  }
  if (context === 'correction_result') {
    const number = questionNumber(text);
    if (number && /^(读|讲|解释|看看|第)/.test(text)) return { type: 'read_check', number };
    if (/^(再说一遍|重读结果|读结果)$/.test(text)) return { type: 'read_summary' };
  }
  if (context === 'finish_confirm' && text === '确认结束作业') return { type: 'finish_homework' };
  if (context === 'parent_correction_request' && /^(允许批改|同意批改)$/.test(text))
    return { type: 'review_correction_access' };
  if (context === 'parent_correction_confirm' && text === '确认允许批改')
    return { type: 'grant_correction_access' };
  return null;
}

export function routeUnmatchedVoiceReply(
  context,
  spoken,
  { active = false, lastHintPrompt = '', candidateId = '' } = {},
) {
  const text = String(spoken || '').trim();
  if (!text) return null;
  const support = active ? parseLearningSupport(text) : null;
  if (support) return support;
  if (
    active &&
    [
      'help_offer',
      'hint_target',
      'hint_followup',
      'hint_attempt_reply',
      'hint_difficulty',
    ].includes(context)
  ) {
    if (context === 'hint_difficulty')
      return {
        type: 'hint',
        question: `${lastHintPrompt ? `${lastHintPrompt}。` : ''}孩子需要帮助：${text}`,
      };
    const reference = parseLocatedQuestionReference(text);
    if (context === 'help_offer') {
      return {
        type: 'hint',
        question: text,
        candidateId: reference ? '' : candidateId,
        spokenReference: Boolean(reference),
      };
    }
    if (context === 'hint_target' || !lastHintPrompt || reference) {
      return { type: 'hint', question: text, spokenReference: Boolean(reference) };
    }
    return { type: 'hint', question: lastHintPrompt, studentThought: text };
  }
  const clarification = {
    setup_camera: '请把作业本放稳后说“检查纸面”；想稍后开始可以说“取消”。',
    correction_ready: '要批改当前作业，请说“开始批改”；也可以先继续做题。',
    batch: '还有作业请翻页；拍完请说“拍完了”，或说“重试”。',
    batch_confirm: '请核对截图。确认无误请说“确认批改”；需要重拍请说“重新拍摄”。',
    finish_confirm: '如果确定结束作业，请说“确认结束作业”；继续做题请说“取消”。',
    correction_result: '想听某道题的批改，请说“讲第几题”；也可以说“读结果”。',
    parent_correction_request: '如果同意开放批改权限，请说“允许批改”；否则可以说“取消”。',
    parent_correction_confirm: '请核对授权范围。同意请说“确认允许批改”；否则可以说“取消”。',
  }[context];
  return { type: 'clarify', message: clarification || '请说出你想做的事，我会继续帮你。' };
}
