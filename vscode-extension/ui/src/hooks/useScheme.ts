import { useEffect, useState } from "react";

const DARK = ["vscode-dark", "vscode-high-contrast"];
const isDark = () => DARK.includes(document.body.dataset.vscodeThemeKind ?? "");

/** Whether VS Code's theme is dark: VS Code names its kind on the webview's body and updates it on theme changes. */
export function useDarkScheme(): boolean {
  const [dark, setDark] = useState(isDark);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(isDark()));
    observer.observe(document.body, { attributes: true, attributeFilter: ["data-vscode-theme-kind"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}
