import React, { useEffect, useRef, useState } from 'react';

// nprogress-style trickle bar pinned to the top edge of the document card.
// A single HTTP call has no true percentage, so the bar is honest about it:
// it jumps fast to ~30%, eases toward ~85% while the call is in flight, and
// only ever reaches 100% when the response actually lands — then fades out.
// The parent applies the 250ms anti-flicker delay (instant composes never
// show a bar at all).
export default function WorkBar({ active }) {
  const [visible, setVisible] = useState(false);
  const [width, setWidth] = useState(0);
  const [fading, setFading] = useState(false);
  const [trans, setTrans] = useState('width .2s ease');
  const timers = useRef([]);

  useEffect(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    const later = (fn, ms) => timers.current.push(setTimeout(fn, ms));

    if (active) {
      setVisible(true);
      setFading(false);
      setTrans('width .2s ease');
      setWidth(30);
      later(() => {
        setTrans('width 6s cubic-bezier(.1,.6,.2,1)');
        setWidth(85);
      }, 220);
    } else {
      // Complete: snap to 100%, fade, unmount.
      setTrans('width .25s ease');
      setWidth(100);
      later(() => setFading(true), 320);
      later(() => {
        setVisible(false);
        setWidth(0);
      }, 950);
    }
    return () => timers.current.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (!visible) return null;
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: 3,
        borderRadius: '14px 14px 0 0',
        overflow: 'hidden',
        opacity: fading ? 0 : 1,
        transition: 'opacity .5s ease',
        pointerEvents: 'none',
        zIndex: 5,
      }}
    >
      <div
        style={{
          height: '100%',
          width: `${width}%`,
          transition: trans,
          background: 'linear-gradient(90deg, #ec4899, #fb7fb8)',
          boxShadow: '0 0 8px rgba(236, 72, 153, .8)',
        }}
      />
    </div>
  );
}
