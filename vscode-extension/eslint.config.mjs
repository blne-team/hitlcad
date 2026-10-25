// The extension (src/) and the panel UI (ui/src/): JavaScript's and TypeScript's recommended rules, and React's
// rules of hooks for the panel. src/lib/schema.ts is generated (scripts/api-types.sh).
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["out/", "node_modules/", "src/lib/schema.ts"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  { files: ["ui/src/**/*.{ts,tsx}"], ...reactHooks.configs.flat.recommended },
  // It reports whether React Compiler could keep hand-written memoization; the panel is not compiled with it.
  { files: ["ui/src/**/*.{ts,tsx}"], rules: { "react-hooks/preserve-manual-memoization": "off" } },
);
