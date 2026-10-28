import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isTaskReadCommand,
  parseLocatedQuestionReference,
  parseQuestionReference,
  parseVoiceReply,
  routeUnmatchedVoiceReply,
} from '../src/voice-intents.js';

test('reading homework tasks does not intercept a question about the paper', () => {
  assert.equal(isTaskReadCommand('读今天任务'), true);
  assert.equal(isTaskReadCommand('播报作业安排'), true);
  assert.equal(isTaskReadCommand('这个字怎么读'), false);
  assert.equal(isTaskReadCommand('读第七题'), false);
});

test('spoken question references preserve major and minor numbering', () => {
  assert.equal(parseQuestionReference('第三大题的第三小题'), '第3大题第3小题');
  assert.equal(parseQuestionReference('第二大题的第一小题的第二小题'), '第2大题第1小题第2小题');
  assert.equal(parseQuestionReference('第二大题的第3小题的第(1)小题'), '第2大题第3小题第1小题');
  assert.equal(parseQuestionReference('帮我看第 12 大题第（二十三）小题'), '第12大题第23小题');
  assert.equal(parseQuestionReference('第七题怎么做'), '第7题');
  assert.equal(parseQuestionReference('第3题第2小题'), '第3题第2小题');
  assert.equal(parseQuestionReference('第三小题'), '第3小题');
  assert.equal(parseQuestionReference('第3题和第4题'), null);
  assert.equal(parseQuestionReference('第一小题和第二小题'), null);
  assert.equal(parseLocatedQuestionReference('右页第一题'), '右页第1题');
  assert.equal(parseLocatedQuestionReference('请看左边第3题第2小题'), '左页第3题第2小题');
  assert.equal(parseLocatedQuestionReference('左页还是右页第一题'), null);
});

test('short reply understands a numbered hint without opening another function', () => {
  assert.deepEqual(parseVoiceReply('hint_target', '第七题。'), { type: 'hint', question: '第7题' });
  assert.deepEqual(parseVoiceReply('help_offer', '第 12 题'), { type: 'hint', question: '第12题' });
  assert.equal(parseVoiceReply('hint_target', '好'), null);
  assert.deepEqual(parseVoiceReply('help_offer', '要提示'), { type: 'ask_target' });
  assert.deepEqual(parseVoiceReply('hint_target', '第三大题的第三小题'), {
    type: 'hint',
    question: '第3大题第3小题',
  });
  assert.deepEqual(parseVoiceReply('hint_target', '第二大题的第一小题的第二小题'), {
    type: 'hint',
    question: '第2大题第1小题第2小题',
  });
  assert.deepEqual(parseVoiceReply('hint_target', '右页第一题'), {
    type: 'hint',
    question: '右页第1题',
  });
});

test('a hint follow-up lets the child try, request a paper check, or continue', () => {
  assert.deepEqual(parseVoiceReply('hint_followup', '我先试试'), {
    type: 'try_independently',
  });
  assert.deepEqual(parseVoiceReply('hint_followup', '帮我看看'), { type: 'check_attempt' });
  assert.deepEqual(parseVoiceReply('hint_followup', '我写好了'), { type: 'check_attempt' });
  assert.deepEqual(parseVoiceReply('hint_followup', '继续提示'), { type: 'next_hint' });
  assert.equal(parseVoiceReply('hint_followup', '我想先画图'), null);
  assert.deepEqual(parseVoiceReply('hint_attempt_reply', '继续提示'), { type: 'next_hint' });
});

test('free-form replies after a hint or help offer go to the tutoring model', () => {
  assert.deepEqual(
    routeUnmatchedVoiceReply('hint_followup', '我觉得先用乘法，但是这里为什么要加一', {
      active: true,
      lastHintPrompt: '第三题怎么做',
    }),
    {
      type: 'hint',
      question: '第三题怎么做',
      studentThought: '我觉得先用乘法，但是这里为什么要加一',
    },
  );
  assert.deepEqual(
    routeUnmatchedVoiceReply('hint_attempt_reply', '左边第六题我想问一下', {
      active: true,
      lastHintPrompt: '第三题怎么做',
    }),
    { type: 'hint', question: '左边第六题我想问一下', spokenReference: true },
  );
  assert.deepEqual(
    routeUnmatchedVoiceReply('help_offer', '这个地方我不知道为什么要进位', {
      active: true,
      candidateId: 'candidate-1',
    }),
    {
      type: 'hint',
      question: '这个地方我不知道为什么要进位',
      candidateId: 'candidate-1',
      spokenReference: false,
    },
  );
  assert.deepEqual(routeUnmatchedVoiceReply('hint_target', '划线的这一问', { active: true }), {
    type: 'hint',
    question: '划线的这一问',
    spokenReference: false,
  });
});

test('unclear confirmation asks for the exact safe action without claiming speech failed', () => {
  for (const context of ['batch_confirm', 'finish_confirm', 'parent_correction_confirm']) {
    const fallback = routeUnmatchedVoiceReply(context, '好的', { active: true });
    assert.equal(fallback.type, 'clarify');
    assert.doesNotMatch(fallback.message, /没有听懂|没听懂/);
  }
  assert.deepEqual(parseVoiceReply('help_offer', '我自己来'), { type: 'decline' });
  assert.deepEqual(parseVoiceReply('parent_correction_request', '不允许批改'), {
    type: 'decline',
  });
});

test('grading and parent access need explicit confirmation words', () => {
  assert.deepEqual(parseVoiceReply('batch_confirm', '确认批改'), { type: 'submit_batch' });
  assert.equal(parseVoiceReply('batch_confirm', '好的'), null);
  assert.equal(parseVoiceReply('batch_confirm', '不要确认批改'), null);
  assert.deepEqual(parseVoiceReply('parent_correction_request', '允许批改'), {
    type: 'review_correction_access',
  });
  assert.equal(parseVoiceReply('parent_correction_confirm', '允许批改'), null);
  assert.deepEqual(parseVoiceReply('parent_correction_request', '不允许批改'), {
    type: 'decline',
  });
  assert.deepEqual(parseVoiceReply('parent_correction_confirm', '确认允许批改'), {
    type: 'grant_correction_access',
  });
  assert.deepEqual(parseVoiceReply('batch_confirm', '不要'), { type: 'decline' });
});

test('the child can request correction without identifying a page or subject', () => {
  assert.deepEqual(parseVoiceReply('setup_camera', '准备好了'), { type: 'check_paper' });
  assert.deepEqual(parseVoiceReply('setup_camera', '检查字面'), { type: 'check_paper' });
  assert.deepEqual(parseVoiceReply('setup_camera', '检查页面'), { type: 'check_paper' });
  assert.equal(parseVoiceReply('hint_target', '检查字面'), null);
  assert.deepEqual(parseVoiceReply('correction_ready', '开始批改'), { type: 'open_correction' });
  assert.equal(parseVoiceReply('correction_ready', '批改右页'), null);
  assert.equal(parseVoiceReply('batch', '科目语文'), null);
  assert.equal(parseVoiceReply('batch', '页码第43页'), null);
});

test('one-page and multi-page collection share the same short commands', () => {
  assert.deepEqual(parseVoiceReply('batch', '确认批改'), { type: 'submit_batch' });
  assert.deepEqual(parseVoiceReply('batch', '拍完了'), { type: 'review_batch' });
  assert.deepEqual(parseVoiceReply('batch', '重试'), { type: 'retry_page' });
  assert.deepEqual(parseVoiceReply('batch', '删除上一张'), { type: 'remove_last_page' });
  assert.equal(parseVoiceReply('batch_confirm', '拍完了'), null);
  assert.deepEqual(parseVoiceReply('batch_confirm', '确认批改'), { type: 'submit_batch' });
  assert.deepEqual(parseVoiceReply('batch_confirm', '重新拍摄'), {
    type: 'restart_collection',
  });
});
