import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = { "/api": { target: process.env.TERRA_API_TARGET || "http://127.0.0.1:8512", changeOrigin: true } };

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: apiProxy,
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  preview: { proxy: apiProxy },
  plugins: [react()],
});
