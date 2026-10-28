import assert from 'node:assert/strict';
import test from 'node:test';
import { sameSubject } from '../src/subject-matching.js';

test('matches the whole subject after explicit K12 grade, stage, and volume descriptors', () => {
  for (const [expected, detected] of [
    ['数学', '四年级奥数'],
    ['四年级奥数', '数学'],
    ['小学四年级数学上册', '数学'],
    ['数学', '小学 四年级：数学（下册）'],
    ['物理', '初二物理'],
    ['初中物理上册', '高二物理'],
    ['物理', '高中二年级物理第二册'],
    ['语文', '七年级语文下册'],
    ['英语', '英文'],
    ['英语', '12年级 英文 (上册)'],
    [' 数学 ', '数学'],
  ]) {
    assert.equal(sameSubject(expected, detected), true, `${expected} / ${detected}`);
  }
});

test('supports arbitrary custom subjects by exact name rather than a subject whitelist', () => {
  for (const [expected, detected] of [
    ['机器人编程', '机器人编程'],
    ['国际象棋', '小学四年级国际象棋上册'],
    ['天文观测', '天文观测'],
    ['AI', 'AI'],
    ['画', '画'],
    ['数', '数'],
    ['a', 'a'],
    ['数学与语文', '数学与语文'],
  ]) {
    assert.equal(sameSubject(expected, detected), true, `${expected} / ${detected}`);
  }
  for (const [expected, detected] of [
    ['机器人编程', '机器人'],
    ['国际象棋', '象棋'],
    ['趣味数学', '数学'],
    ['AI', 'ai'],
    ['机器人 编程', '机器人编程'],
    ['数学实验', '数学'],
    ['天文观测', '天文学'],
  ]) {
    assert.equal(sameSubject(expected, detected), false, `${expected} / ${detected}`);
  }
});

test('does not merge distinct subjects or match one component of a combined subject', () => {
  for (const [expected, detected] of [
    ['语文', '数学'],
    ['数学', '四年级语文'],
    ['初二物理', '高二化学'],
    ['数学', '数学与语文'],
    ['语文', '数学与语文'],
    ['数学与语文', '数学'],
    ['数学', '四年级奥数与语文下册'],
    ['数学', '数学竞赛'],
    ['数学', '奥数训练'],
    ['数', '数学'],
    ['英语', '英语口语'],
    ['数学', '13年级数学'],
    ['物理', '高四物理'],
  ]) {
    assert.equal(sameSubject(expected, detected), false, `${expected} / ${detected}`);
  }
});

test('rejects empty, descriptor-only, and non-string subjects', () => {
  for (const value of [
    '',
    ' ',
    '小学',
    '四年级',
    '上册',
    null,
    undefined,
    12,
    {},
  ]) {
    assert.equal(sameSubject(value, value), false, String(value));
    assert.equal(sameSubject('数学', value), false, String(value));
    assert.equal(sameSubject(value, '数学'), false, String(value));
  }
});
