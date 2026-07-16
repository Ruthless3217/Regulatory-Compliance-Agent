import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: { center: true, padding: "1.5rem" },
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        surface: "hsl(var(--surface))",
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        border: "hsl(var(--border))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-fg))",
          50: "hsl(var(--primary-50))",
          100: "hsl(var(--primary-100))",
          500: "hsl(var(--primary-500))",
          600: "hsl(var(--primary-600))",
        },
        sev: {
          critical: "hsl(var(--sev-critical))",
          high: "hsl(var(--sev-high))",
          medium: "hsl(var(--sev-medium))",
          low: "hsl(var(--sev-low))",
        },
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        info: "hsl(var(--info))",
      },
      borderRadius: {
        DEFAULT: "var(--radius)",
        sm: "4px",
        md: "6px",
        lg: "8px",
      },
      boxShadow: {
        card: "var(--shadow-card)",
      },
      fontFamily: {
        serif: ["var(--font-sans)", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        micro: "0.06em",
      },
      keyframes: {
        "slide-down": {
          "0%": { transform: "translateY(-6px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "pulse-select": {
          "0%": { boxShadow: "inset 2px 0 0 hsl(var(--primary))" },
          "50%": { boxShadow: "inset 4px 0 0 hsl(var(--primary))" },
          "100%": { boxShadow: "inset 2px 0 0 hsl(var(--primary))" },
        },
        // Slim indeterminate progress bar: an inner segment (w-1/4) sweeps
        // left→right across its container repeatedly. No real percentage.
        "indeterminate-bar": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(400%)" },
        },
      },
      animation: {
        "slide-down": "slide-down 140ms ease-out",
        "fade-in": "fade-in 200ms ease-out",
        "pulse-select": "pulse-select 800ms ease-in-out",
        "indeterminate-bar": "indeterminate-bar 1.3s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
