import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        aurora: {
          night: "#090A0F",
          panel: "#11131A",
          border: "#2A2F3D",
          text: "#F6F7FB",
          muted: "#A7B0C0",
          cyan: "#62E6FF",
          green: "#70F3B8",
          rose: "#FF7AA2",
          gold: "#F5C86A"
        }
      },
      boxShadow: {
        aurora: "0 18px 70px rgba(98, 230, 255, 0.12)"
      }
    }
  },
  plugins: []
} satisfies Config;
