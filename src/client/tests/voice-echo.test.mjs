import assert from 'node:assert/strict';
import test from 'node:test';
import { likelyPromptEcho } from '../src/voice-echo.js';

test('speech picked up from the prompt is ignored while it is playing', () => {
  const prompt = '请检查纸面，完成后说继续。';
  assert.equal(likelyPromptEcho('请检查纸面', prompt, true), true);
  assert.equal(likelyPromptEcho('继续', prompt, true), true);
  assert.equal(likelyPromptEcho('下两页', '已拍下2页，共2页。', true), true);
});

test('an unrelated answer or a reply after playback can still be used', () => {
  const prompt = '请检查纸面，完成后说继续。';
  assert.equal(likelyPromptEcho('我已经检查好了', prompt, true), false);
  assert.equal(likelyPromptEcho('请检查纸面', prompt, false), false);
});

test('a slightly different transcription of the speaker output is ignored', () => {
  assert.equal(
    likelyPromptEcho(
      '请把作业放好准备好说检查纸面',
      '请把作业放好，准备好后说检查纸面。',
      true,
    ),
    true,
  );
  assert.equal(
    likelyPromptEcho(
      '请把作业准备好说检查纸面',
      '请把作业放好，准备好后说检查纸面。',
      true,
    ),
    true,
  );
  assert.equal(
    likelyPromptEcho('我准备好了现在开始', '请把作业放好，准备好后说检查纸面。', true),
    false,
  );
  assert.equal(likelyPromptEcho('开始', '请说开始作业。', true), true);
  assert.equal(likelyPromptEcho('暂停', '请说开始作业。', true), false);
});
