import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname),
  base: "/",
  build: {
    outDir: path.resolve(__dirname, "../webui_assets"),
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
});
