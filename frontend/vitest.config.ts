// frontend/vitest.config.ts
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // tsconfig sets jsx:"preserve" for Next; the transform needs to be told to
  // actually transform it, which is cheaper than pulling in
  // @vitejs/plugin-react. Vite 8 deprecated the `esbuild` option in favor of
  // `oxc` (its own default transform engine as of Vite 6+), so this is
  // configured natively through `oxc.jsx` rather than the deprecated field —
  // `runtime: "automatic"` is oxc's own default already, but named here for
  // the same reason the brief wanted it explicit on esbuild.
  oxc: { jsx: { runtime: "automatic" } },
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  test: {
    globals: true,
    // Node by default: the anchoring math is pure and must not need a DOM to
    // run. Files that genuinely need one declare `// @vitest-environment jsdom`
    // on their first line, which is why they are named *.dom.test.tsx.
    environment: "node",
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
