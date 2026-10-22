"use client";

import { cloneElement, useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type ReactElement } from "react";
import { createPortal } from "react-dom";
import "./ActionTooltip.css";

type Props = { text: string; children: ReactElement<ButtonHTMLAttributes<HTMLButtonElement>> };

// A portal keeps hints visible even in the first/last row of a scrolling list.
export default function ActionTooltip({ text, children }: Props) {
  const id = useId(), focused = useRef(false), timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [position, setPosition] = useState<{ left: number; top: number; below: boolean } | null>(null);
  const open = position !== null;
  const cancelClose = () => { if (timer.current) { clearTimeout(timer.current); timer.current = null; } };
  const close = () => { cancelClose(); setPosition(null); };
  const show = (button: HTMLButtonElement) => {
    cancelClose();
    const box = button.getBoundingClientRect(), width = Math.min(280, window.innerWidth - 24);
    const below = box.top < 100;
    setPosition({ left: Math.max(12, Math.min(box.right - width, window.innerWidth - width - 12)), top: below ? box.bottom + 8 : box.top - 8, below });
  };
  const leave = () => { cancelClose(); if (!focused.current) timer.current = setTimeout(() => setPosition(null), 100); };
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  useEffect(() => {
    if (!open) return;
    const hide = () => { if (timer.current) clearTimeout(timer.current); setPosition(null); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") hide(); };
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener("scroll", hide, true); window.removeEventListener("resize", hide); window.removeEventListener("keydown", key); };
  }, [open]);

  // eslint-disable-next-line react-hooks/refs -- cloneElement attaches these event handlers; it does not invoke them during render.
  return <>{cloneElement(children, {
    // Avoid displaying a second, delayed native tooltip over the readable hint.
    title: undefined,
    "aria-describedby": open ? [children.props["aria-describedby"], id].filter(Boolean).join(" ") : children.props["aria-describedby"],
    onMouseEnter: event => { children.props.onMouseEnter?.(event); show(event.currentTarget); },
    onMouseLeave: event => { children.props.onMouseLeave?.(event); leave(); },
    onFocus: event => { children.props.onFocus?.(event); focused.current = true; show(event.currentTarget); },
    onBlur: event => { children.props.onBlur?.(event); focused.current = false; leave(); },
    onClick: event => { close(); children.props.onClick?.(event); },
  })}{position && createPortal(<div id={id} role="tooltip" className="action-tooltip-bubble" style={{ left: position.left, top: position.top, transform: position.below ? undefined : "translateY(-100%)" }} onMouseEnter={cancelClose} onMouseLeave={leave}>{text}</div>, document.body)}</>;
}
