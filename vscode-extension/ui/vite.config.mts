import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

// The panel UI, loaded by the extension's webviews (src/lib/panelHtml.ts) from out/ui/ under fixed names.
export default defineConfig({
  root: here("."),
  base: "./", // fonts resolve next to panel.css, wherever the webview serves it from
  plugins: [react()],
  build: {
    outDir: here("../out/ui"),
    emptyOutDir: true,
    // three-cad-viewer bundles its own three.js (~1.4 MB) and is the whole 3D view: one chunk is expected.
    chunkSizeWarningLimit: 1700,
    rollupOptions: {
      input: here("src/main.tsx"),
      output: { entryFileNames: "panel.js", assetFileNames: (a) => (a.names[0]?.endsWith(".css") ? "panel.css" : "[name][extname]") },
    },
  },
});
