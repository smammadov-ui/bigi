import React, { useEffect } from 'react';

// Transient bottom toast. Auto-dismisses after ~1.8s.
export default function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return undefined;
    const t = setTimeout(() => onClose && onClose(), 1800);
    return () => clearTimeout(t);
  }, [message, onClose]);

  if (!message) return null;
  return <div className="toast">{message}</div>;
}
