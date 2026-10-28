import assert from 'node:assert/strict';
import test from 'node:test';
import { choicesForPrompt, markedSpeechParts } from '../src/interaction-copy.js';

test('answer words are visibly distinguished from a spoken request', () => {
  const prompt = '都拍好了就说确认批改；有问题说重新拍摄。';
  const choices = choicesForPrompt('batch_confirm', prompt);
  assert.deepEqual(choices, ['确认批改', '重新拍摄']);
  assert.deepEqual(
    markedSpeechParts(prompt, choices)
      .filter((part) => part.highlighted)
      .map((part) => part.text),
    choices,
  );
});

test('a noninteractive announcement has no highlighted answer', () => {
  assert.deepEqual(choicesForPrompt('unknown', '作业已暂停'), []);
  assert.deepEqual(markedSpeechParts('作业已暂停', []), [
    { text: '作业已暂停', highlighted: false },
  ]);
});

test('a hint offer exposes a general action instead of a fixed question number', () => {
  const prompt = '可以说第几题，也可以说要提示，或者说不用。';
  assert.deepEqual(choicesForPrompt('help_offer', prompt), ['要提示', '不用']);
  assert.deepEqual(choicesForPrompt('hint_target', '例如说第七题。'), []);
});

test('the same spoken action is offered as a button only once', () => {
  const parts = markedSpeechParts('说继续提示，没听清还可以说继续提示。', ['继续提示']);
  assert.deepEqual(
    parts.filter((part) => part.highlighted).map((part) => part.text),
    ['继续提示'],
  );
});

test('hint caption stays conversational without command choices', () => {
  const caption = '先找条件。孩子说“帮我看看”或“继续提示”也不出现固定口令菜单。';
  assert.deepEqual(choicesForPrompt('hint_followup', caption), []);
});
