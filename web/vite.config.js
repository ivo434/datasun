import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// BASE_PATH permite servir bajo un subdirectorio (p. ej. GitHub Pages)
export default defineConfig({
  plugins: [react()],
  base: process.env.BASE_PATH || "/",
});
