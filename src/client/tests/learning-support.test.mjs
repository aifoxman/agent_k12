import assert from 'node:assert/strict';
import test from 'node:test';
import {
  parseLearningSupport,
  parseVoiceReply,
  routeUnmatchedVoiceReply,
} from '../src/voice-intents.js';

test('explicit fatigue offers a break without being recorded as a solution idea', () => {
  for (const text of ['我太累了', '我想休息一下', '我不想做了']) {
    assert.equal(parseLearningSupport(text)?.type, 'offer_rest');
    assert.equal(parseVoiceReply('hint_followup', text)?.type, 'offer_rest');
    const result = routeUnmatchedVoiceReply('hint_attempt_reply', text, {
      active: true,
      lastHintPrompt: '第3题',
    });
    assert.equal(result.type, 'offer_rest');
    assert.equal(result.studentThought, undefined);
  }
});

test('frustration requests a specific difficulty instead of escalating a hint', () => {
  for (const text of ['我就是不会', '我做不出来', '我不知道怎么开始'])
    assert.equal(parseLearningSupport(text)?.type, 'clarify_difficulty');
  const reply = routeUnmatchedVoiceReply('hint_difficulty', '题意没读懂', {
    active: true,
    lastHintPrompt: '第3题',
  });
  assert.equal(reply.type, 'hint');
  assert.match(reply.question, /第3题.*题意没读懂/);
  assert.equal(reply.studentThought, undefined);
});

test('worksheet stories and normal mathematical ideas are not interpreted as fatigue', () => {
  for (const text of [
    '文中的小明说我太累了',
    '为什么作者说我不想做了',
    '我先把两段相加',
    '累了这个词怎么读',
  ])
    assert.equal(parseLearningSupport(text), null);
});

test('choosing to work alone ends tutoring without becoming a new hint request', () => {
  for (const text of ['我自己来', '我先自己做', '让我自己试试'])
    assert.equal(parseVoiceReply('hint_followup', text)?.type, 'try_independently');
  assert.equal(parseVoiceReply('help_offer', '我自己来')?.type, 'decline');
});
