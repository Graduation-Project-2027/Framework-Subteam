import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          ui: ["radix-ui", "motion/react", "lucide-react"],
          ros: ["roslib"],
        },
      },
    },
  },
});
