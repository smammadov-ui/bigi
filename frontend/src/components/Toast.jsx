import React, { useEffect, useRef } from 'react';

// Transient bottom toast. Auto-dismisses after ~1.8s. The dismiss callback is
// held in a ref so the timer restarts only when the MESSAGE changes, not on
// every parent re-render (audit B19 — stage cycling re-rendered Home ~1.3s and
// kept resetting the timeout, so toasts lingered).
export default function Toast({ message, onClose }) {
  const cb = useRef(onClose);
  cb.current = onClose;

  useEffect(() => {
    if (!message) return undefined;
    const t = setTimeout(() => cb.current && cb.current(), 1800);
    return () => clearTimeout(t);
  }, [message]);

  if (!message) return null;
  return <div className="toast">{message}</div>;
}
