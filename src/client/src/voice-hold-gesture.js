export function setVoiceHoldGestureActive(active) {
  if (active) window.getSelection?.()?.removeAllRanges();
  try {
    window.PaperVoice?.setHoldGestureActive?.(active);
  } catch {
    // Keep the web voice button usable if an older Android bridge lacks this method.
  }
}

export function guardVoiceHoldSelection(button) {
  if (!button) return () => {};
  const preventSelection = (event) => event.preventDefault();
  for (const name of ['selectstart', 'contextmenu', 'dragstart']) {
    button.addEventListener(name, preventSelection);
  }
  return () => {
    for (const name of ['selectstart', 'contextmenu', 'dragstart']) {
      button.removeEventListener(name, preventSelection);
    }
  };
}
