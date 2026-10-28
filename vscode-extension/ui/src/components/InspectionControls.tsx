import { useEffect, useRef } from "react";
import type { InspectionAction } from "../lib/inspection";

/** Shortcuts for inspecting the selection, or the parts named by a finding. */
export function InspectionControls({ count, onOpen, onAction, onParameters }: { count: number; onOpen: () => void; onAction: (action: InspectionAction) => void; onParameters?: () => void }) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: PointerEvent) => {
      if (e.target instanceof Node && menu.current && !menu.current.contains(e.target)) menu.current.open = false;
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  return (
    <details ref={menu} className="inspection-tools" onToggle={(e) => {
      if (e.currentTarget.open) onOpen();
    }} onKeyDown={(e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.currentTarget.open = false;
        e.currentTarget.querySelector("summary")?.focus();
      }
    }}>
      <summary className="viewer-tool-button inspect-button" role="button" aria-label="Inspect model" title="Inspect model">
        <svg width="26" height="26" viewBox="0 0 26 26" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m10 3 7 4-7 4-7-4 7-4ZM3 7v8l5 3M10 11v5M17 7v3" />
          <circle cx="16" cy="17" r="4.5" className="inspect-lens" />
          <path d="m19.5 20.5 4 4" className="inspect-lens" />
        </svg>
      </summary>
      <div className="inspection-controls" role="toolbar" aria-label="Inspect model">
        <span className="muted tiny">{count ? `${count} part${count === 1 ? "" : "s"}` : "Click a part in the model to inspect it."}</span>
        <button type="button" className="button small" disabled={!count} onClick={() => onAction("focus")}>Fit selection</button>
        <button type="button" className="button small" disabled={!count} onClick={() => onAction("isolate")}>Isolate</button>
        <span className="muted tiny">Section</span>
        {(["X", "Y", "Z"] as const).map((axis) => (
          <button key={axis} type="button" className="button small" title={`Cut through the centre along ${axis}`} onClick={() => onAction(`section${axis}`)}>{axis}</button>
        ))}
        <button type="button" className="button small" onClick={() => onAction("restore")}>Show all</button>
        {onParameters && <button type="button" className="button small inspection-parameters" disabled={!count} onClick={() => {
          onParameters();
          if (menu.current) {
            menu.current.open = false;
            menu.current.querySelector("summary")?.focus();
          }
        }}>Edit parameters</button>}
      </div>
    </details>
  );
}
