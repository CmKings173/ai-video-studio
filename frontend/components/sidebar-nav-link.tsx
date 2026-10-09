"use client";

import { useEffect, useId, useRef, useState, type ReactNode, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";

export function SidebarNavLink({ href, label, description, active, children }: {
  href: string; label: string; description: string; active: boolean; children: ReactNode;
}) {
  const tooltipId = useId();
  const [position, setPosition] = useState<{ left: number; top: number; width: number } | null>(null);
  const hovered = useRef(false);
  const focused = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelHide = () => { if (timer.current) clearTimeout(timer.current); timer.current = null; };
  const hide = () => { hovered.current = false; cancelHide(); setPosition(null); };
  const leave = () => {
    cancelHide();
    timer.current = setTimeout(() => { if (!hovered.current && !focused.current) setPosition(null); }, 150);
  };
  const show = (event: SyntheticEvent<HTMLAnchorElement>) => {
    cancelHide();
    const rect = event.currentTarget.getBoundingClientRect();
    const width = Math.min(224, window.innerWidth - 16);
    setPosition({
      left: Math.max(8, Math.min(rect.right + 8, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(rect.top, window.innerHeight - 100)),
      width,
    });
  };
  useEffect(() => {
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, []);
  useEffect(() => {
    if (!position) return;
    const dismiss = () => {
      hovered.current = false;
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      setPosition(null);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") dismiss(); };
    window.addEventListener("keydown", escape);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      window.removeEventListener("keydown", escape);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [position]);

  return <>
    <Link href={href} aria-label={label} aria-current={active ? "page" : undefined}
      aria-describedby={position ? tooltipId : undefined} className={active ? "active" : ""}
      onMouseEnter={event => { hovered.current = true; show(event); }}
      onMouseLeave={() => { hovered.current = false; leave(); }}
      onFocus={event => { focused.current = true; show(event); }}
      onBlur={() => { focused.current = false; leave(); }} onClick={hide}
      onKeyDown={event => { if (event.key === "Escape") hide(); }}>
      {children}
    </Link>
    {position ? createPortal(<div id={tooltipId} role="tooltip" style={position}
      onMouseEnter={() => { hovered.current = true; cancelHide(); }}
      onMouseLeave={() => { hovered.current = false; leave(); }}
      className="fixed z-[100] rounded-md border border-[#2c3038] bg-[#22252b] px-3 py-2 text-xs text-[#f1f3f5] shadow-lg">
      <strong className="block font-semibold">{label}</strong>
      <span className="mt-1 block text-[#c3c6d7]">{description}</span>
    </div>, document.body) : null}
  </>;
}
