import type { Config } from "tailwindcss";

// Waypost design tokens - lifted from the existing app, made consistent
// (single radius scale, single shadow depth, single border weight)
// instead of drifting per-component like the original vanilla HTML did.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#14140f",       // near-black, used for text + borders (not pure #000)
        cream: "#fdfbf3",     // page background
        signal: "#ffe14d",    // the one accent - yellow. Used sparingly, not everywhere.
        muted: "#4a473e",
      },
      fontFamily: {
        // Falls back to system fonts until you enable next/font/google
        // in app/layout.tsx (see comment there) - swap in
        // var(--font-grotesk) / var(--font-mono) once that's on.
        display: ["'Space Grotesk'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      boxShadow: {
        // one hard-offset shadow depth, used consistently instead of
        // the 3 slightly-different depths in the original
        offset: "4px 4px 0 #14140f",
        "offset-sm": "2px 2px 0 #14140f",
        "offset-lg": "6px 6px 0 #14140f",
        "offset-signal": "6px 6px 0 #ffe14d",
      },
      borderRadius: {
        card: "14px",
        pill: "100px",
      },
    },
  },
  plugins: [],
};
export default config;
