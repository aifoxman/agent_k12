const SUBJECT_ALIASES = new Map([
  ['奥数', '数学'],
  ['英文', '英语'],
]);

const GRADE = '(?:十一|十二|[一二三四五六七八九十]|1[0-2]|[1-9])年级';
const PREFIX = new RegExp(
  `^(?:(?:小学|初中|高中)(?:\\s*${GRADE})?|${GRADE}|初[一二三123]|高[一二三123])[\\s·:：/-]*`,
  'u',
);
const VOLUME = '(?:上册|下册|第(?:[一二三四五六七八九十]{1,3}|[1-9][0-9]?)册)';
const SUFFIX = new RegExp(`(?:[\\s·:：/-]*${VOLUME}|\\s*[（(]${VOLUME}[）)])$`, 'u');

function subjectName(value) {
  if (typeof value !== 'string') return null;
  const name = value.trim().replace(PREFIX, '').replace(SUFFIX, '').trim();
  if (!name) return null;
  return SUBJECT_ALIASES.get(name) ?? name;
}

// Match whole names after explicit school descriptors; custom subjects stay exact.
export function sameSubject(expected, detected) {
  const expectedName = subjectName(expected);
  const detectedName = subjectName(detected);
  return expectedName !== null && detectedName !== null && expectedName === detectedName;
}
