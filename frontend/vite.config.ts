import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const root = path.resolve(__dirname, "..");
const cert = path.join(root, "cert.pem");
const key = path.join(root, "cert-key.pem");

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: false,
    https: fs.existsSync(cert) && fs.existsSync(key)
      ? {
          cert: fs.readFileSync(cert),
          key: fs.readFileSync(key)
        }
      : undefined
  }
});

