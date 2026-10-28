import assert from 'node:assert/strict';
import test from 'node:test';

import {
  completedVoiceReply,
  createReplyPause,
} from '../src/voice-turn.js';

test('short breath without new words keeps one silence deadline', () => {
  let now = 1000;
  const pause = createReplyPause(4000, () => now);
  assert.equal(pause.noteTranscript('我在看题'), true);
  assert.equal(pause.deadline, 5000);
  now = 3800;
  assert.equal(pause.ensureDeadline(), 5000);
  assert.equal(pause.noteTranscript('我在看题'), false);
  assert.equal(pause.deadline, 5000);
  now = 4800;
  assert.equal(pause.noteTranscript('我在看题 我想一下'), true);
  assert.equal(pause.deadline, 8800);
  now = 8900;
  assert.equal(now >= pause.deadline, true);
});

test('confirmed words complete a reply after a later service error', () => {
  assert.equal(
    completedVoiceReply({ partial: '我在看题', error: '系统语音识别服务暂不可用' }),
    '我在看题',
  );
  assert.equal(completedVoiceReply({ text: '我在看题 我想一下' }), '我在看题 我想一下');
  assert.equal(completedVoiceReply({ partial: '确认允许批改' }), '');
});

test('recognition error without confirmed words does not become a reply', () => {
  assert.equal(completedVoiceReply({ error: '系统语音识别服务暂不可用' }), '');
});
