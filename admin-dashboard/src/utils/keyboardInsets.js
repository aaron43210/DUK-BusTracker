// keyboardInsets.js
// Keeps the focused input visible when the on-screen keyboard opens on
// touch devices (tablets/phones used to manage stops, trips, etc.).
// Tracks keyboard height via the VisualViewport API into the `--kb` CSS var
// and scrolls the focused field into view. No-ops where VisualViewport is absent.

let _installed = false;

export function initKeyboardInsets() {
  if (_installed || typeof window === 'undefined') return;
  _installed = true;

  const root = document.documentElement;
  const vv = window.visualViewport;
  const setKb = (px) => root.style.setProperty('--kb', `${Math.max(0, Math.round(px))}px`);
  setKb(0);

  const isEditable = (el) =>
    el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);

  if (vv) {
    const onChange = () => {
      const kb = window.innerHeight - vv.height - vv.offsetTop;
      setKb(kb > 60 ? kb : 0);
    };
    vv.addEventListener('resize', onChange);
    vv.addEventListener('scroll', onChange);
  }

  document.addEventListener('focusin', (e) => {
    if (!isEditable(e.target)) return;
    setTimeout(() => {
      try { e.target.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      catch (_) { e.target.scrollIntoView(false); }
    }, 300);
  });

  document.addEventListener('focusout', (e) => {
    if (!isEditable(e.target)) return;
    setTimeout(() => { if (!isEditable(document.activeElement)) setKb(0); }, 100);
  });
}
