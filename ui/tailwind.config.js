/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "bg-base": "var(--bg-base)",
        "bg-sidebar": "var(--bg-sidebar)",
        "bg-surface": "var(--bg-surface)",
        "bg-hover": "var(--bg-hover)",
        "bg-player": "var(--bg-player)",
        "base": "var(--bg-base)",
        "sidebar": "var(--bg-sidebar)",
        "surface": "var(--bg-surface)",
        "hover": "var(--bg-hover)",
        "player": "var(--bg-player)",
        "border": "var(--border)",
        "border-subtle": "var(--border)",
        "border-light": "rgba(255, 255, 255, 0.12)",
        "text-1": "var(--text-1)",
        "text-2": "var(--text-2)",
        accent: "var(--accent)",
        liked: "var(--liked)",
        downloaded: "var(--downloaded)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "Consolas", "monospace"],
      },
      transitionTimingFunction: {
        smooth: "cubic-bezier(0.2, 0, 0, 1)",
      },
      height: {
        titlebar: "44px",
        player: "76px",
      },
      width: {
        sidebar: "220px",
        queue: "300px",
      },
    },
  },
  plugins: [],
};
