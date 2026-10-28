import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({
  configFile: false,
  root: fileURLToPath(new URL('..', import.meta.url)),
  esbuild: { jsx: 'automatic' },
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: 'custom',
  logLevel: 'silent',
});
after(() => server.close());

const { TeachingScratch } = await server.ssrLoadModule('/src/teaching-ink.jsx');
const prose = (text) => ({ kind: 'text', role: 'prose', text });
const render = (props) => renderToStaticMarkup(createElement(TeachingScratch, props));

test('released reasoning and a diagram stay visible while the speech caption is present', () => {
  const reasons = Array.from({ length: 20 }, (_, index) => `已讲清的理由${index}：逐项检查条件。`);
  const beats = reasons.map((reason, index) => ({
    step_id: index + 1,
    scratch: [
      prose(reason),
      ...(index === 0 ? [{ kind: 'rect', x: 10, y: 10, w: 70, h: 75 }] : []),
    ],
  }));
  const html = render({ beats, captionVisible: true });
  for (const reason of reasons) assert.ok(html.includes(reason), reason);
  assert.match(html, /class="teaching-scratch-diagram"/u);
  assert.doesNotMatch(html, /<button|查看完整示意|展开|收起/u);
});

test('step replay keeps already released reasons without revealing a future step', () => {
  const beats = [
    { step_id: 1, scratch: [prose('先比较已知量。')] },
    { step_id: 2, scratch: [prose('再说明为什么这样计算。')] },
    { step_id: 3, scratch: [prose('最后才出现的检验理由。')] },
  ];
  const html = render({ beats, playbackBeatIndex: 1, inline: true });
  assert.match(html, /先比较已知量。/u);
  assert.match(html, /再说明为什么这样计算。/u);
  assert.doesNotMatch(html, /最后才出现的检验理由。/u);
  assert.equal((html.match(/class="teaching-scratch-step"/gu) || []).length, 2);
});

test('the latest label replaces the old value while prior reasoning remains on the sheet', () => {
  const beats = [
    {
      step_id: 1,
      scratch: [
        prose('先记录原来的条件。'),
        { kind: 'text', role: 'label', text: '原量', x: 10, y: 25 },
      ],
    },
    {
      step_id: 2,
      scratch: [
        prose('再记录变化后的条件。'),
        { kind: 'text', role: 'label', text: '新量', x: 10, y: 25 },
      ],
    },
  ];
  const html = render({ beats });
  assert.match(html, /先记录原来的条件。/u);
  assert.match(html, /再记录变化后的条件。/u);
  assert.match(html, />新量<\/tspan>/u);
  assert.doesNotMatch(html, />原量<\/tspan>/u);
});

test('hidden teaching and a not-yet-released scratch sheet render no content', () => {
  const beats = [{ step_id: 1, scratch: [prose('尚未展示的草稿。')] }];
  assert.equal(render({ beats, show: false }), '');
  assert.equal(render({ beats, playbackBeatIndex: -1 }), '');
  assert.equal(render({ beats: [] }), '');
});
