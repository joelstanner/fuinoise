import { defineConfig } from "vite";

export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    minify: true,
    outDir: "../fuinoise_live/static/fuinoise_live/organizer",
    emptyOutDir: true,
    lib: {
      entry: "src/main.jsx",
      formats: ["es"],
      fileName: () => "workspace.js",
    },
    cssCodeSplit: false,
    rollupOptions: { output: { assetFileNames: "workspace.[ext]" } },
  },
});
