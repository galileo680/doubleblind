import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "doubleblind.theme";

function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    return "dark";
  }
  return "dark";
}

export function useTheme(forced?: Theme): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const active = forced ?? theme;
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", active);
  }, [active]);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(KEY, next);
    } catch {
      setTheme(next);
    }
    setTheme(next);
  };
  return [active, toggle];
}
