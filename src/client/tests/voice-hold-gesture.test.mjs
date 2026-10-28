import assert from 'node:assert/strict';
import test from 'node:test';

import { guardVoiceHoldSelection, setVoiceHoldGestureActive } from '../src/voice-hold-gesture.js';

test('holding voice blocks selection and context menus without affecting other text', () => {
  const button = new EventTarget();
  const removeGuards = guardVoiceHoldSelection(button);
  for (const name of ['selectstart', 'contextmenu', 'dragstart']) {
    const event = new Event(name, { cancelable: true });
    button.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true, name);
  }
  removeGuards();
  const normalMenu = new Event('contextmenu', { cancelable: true });
  button.dispatchEvent(normalMenu);
  assert.equal(normalMenu.defaultPrevented, false);
});

test('Android long press suppression follows the actual voice hold', () => {
  const events = [];
  globalThis.window = {
    getSelection: () => ({ removeAllRanges: () => events.push('clear selection') }),
    PaperVoice: { setHoldGestureActive: (active) => events.push(active) },
  };
  setVoiceHoldGestureActive(true);
  setVoiceHoldGestureActive(false);
  assert.deepEqual(events, ['clear selection', true, false]);
});
