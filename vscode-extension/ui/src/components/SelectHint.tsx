import { type ReactNode, useState } from "react";
import { selectionHint, toExtension } from "../lib/host";

/** How to select, along the bottom of a 3D view, until the human closes it: then not again, in any view
 * (`hitlcad.selectionHint`, which turns it back on). */
export function SelectHint({ children }: { children: ReactNode }) {
  const [shown, setShown] = useState(selectionHint);
  if (!shown) return null;
  const hide = () => {
    setShown(false);
    toExtension({ type: "hitlcad.hideSelectionHint" });
  };
  return (
    <p className="viewport-hint select-hint">
      {children}
      <button type="button" className="hint-close" aria-label="Hide this hint" title="Hide this hint" onClick={hide}>
        ×
      </button>
    </p>
  );
}
