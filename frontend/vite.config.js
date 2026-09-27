import { defineConfig } from "vite";

// 相对路径构建，便于由后端在任意子路径下直接托管 dist/
export default defineConfig({
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
