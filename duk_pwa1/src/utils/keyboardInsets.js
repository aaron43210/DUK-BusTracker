// keyboardInsets.js
// Keeps the focused input visible when the on-screen keyboard opens.
//
// On iOS the soft keyboard does NOT shrink the layout viewport, so a fixed
// height:100dvh; overflow:hidden app-shell can leave a focused field hidden
// behind the keyboard with no way to scroll to it. This module:
//   1. Tracks the keyboard height via the VisualViewport API and exposes it as
//      the CSS var `--kb` on :root, so scroll containers can pad their bottom
//      (padding-bottom: calc(var(--sab) + var(--kb))).
//   2. Scrolls the focused input into the centre of the *visible* area once the
//      keyboard has animated in.
//
// Safe to call once at startup; it no-ops where VisualViewport is unavailable.

let _installed = false;

export function initKeyboardInsets() {
  if (_installed || typeof window === 'undefined') return;
  _installed = true;

  const root = document.documentElement;
  const vv = window.visualViewport;

  const setKb = (px) => {
    root.style.setProperty('--kb', `${Math.max(0, Math.round(px))}px`);
  };
  setKb(0);

  const isEditable = (el) =>
    el && (
      el.tagName === 'INPUT' ||
      el.tagName === 'TEXTAREA' ||
      el.tagName === 'SELECT' ||
      el.isContentEditable
    );

  if (vv) {
    const onViewportChange = () => {
      // Keyboard height = layout viewport height not covered by the visual
      // viewport (plus any offset pushed off the top).
      const kb = window.innerHeight - vv.height - vv.offsetTop;
      setKb(kb > 60 ? kb : 0); // ignore sub-60px jitter (URL bar, etc.)
    };
    vv.addEventListener('resize', onViewportChange);
    vv.addEventListener('scroll', onViewportChange);
  }

  // Bring the focused field into view after the keyboard animation settles.
  document.addEventListener('focusin', (e) => {
    const el = e.target;
    if (!isEditable(el)) return;
    setTimeout(() => {
      try {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } catch (_) {
        el.scrollIntoView(false);
      }
    }, 300);
  });

  document.addEventListener('focusout', (e) => {
    if (!isEditable(e.target)) return;
    // Let the keyboard close, then reset the inset.
    setTimeout(() => {
      const active = document.activeElement;
      if (!isEditable(active)) setKb(0);
    }, 100);
  });
}
