import type { InspectorTab } from "../lib/designUi";
import { TABS, type TabContext } from "./InspectorView";

/** The Design view's tabs the 3D editor also shows, beside the model. */
const REVIEW: InspectorTab[] = ["report", "parameters"];

/**
 * The 3D editor's review panel, floating beside the model: the build's results and the design's parameters,
 * the Design view's own tabs, folded to their names (with what needs attention) until one is opened.
 */
export function ReviewPanel({ ctx, open, onToggle }: { ctx: TabContext; open: InspectorTab | null; onToggle: (tab: InspectorTab | null) => void }) {
  const tabs = REVIEW.map((id) => TABS.find((t) => t.id === id)!);
  const shown = tabs.find((t) => t.id === open);
  return (
    <aside className={`review${shown ? " open" : ""}`} aria-label="Review">
      <div className="review-tabs" role="tablist">
        {tabs.map((t) => {
          const count = t.badge?.(ctx) ?? 0;
          return (
            <button key={t.id} type="button" role="tab" aria-selected={t === shown} className={t === shown ? "review-tab on" : "review-tab"}
              onClick={() => onToggle(t === shown ? null : t.id)}>
              {t.label}
              {count > 0 && <span className="tab-count">{count}</span>}
            </button>
          );
        })}
      </div>
      {shown && <div className="review-body">{shown.render(ctx)}</div>}
    </aside>
  );
}
