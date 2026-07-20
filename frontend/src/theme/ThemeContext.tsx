import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "raffle_theme";

interface ThemeContextValue {
  resolvedTheme: Theme;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme | null>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  });

  useEffect(() => {
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem(STORAGE_KEY, theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
      localStorage.removeItem(STORAGE_KEY);
    }
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const resolved = current ?? (systemPrefersDark() ? "dark" : "light");
      return resolved === "dark" ? "light" : "dark";
    });
  }, []);

  const resolvedTheme: Theme = theme ?? (systemPrefersDark() ? "dark" : "light");

  return (
    <ThemeContext.Provider value={{ resolvedTheme, toggleTheme }}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme должен использоваться внутри ThemeProvider");
  return ctx;
}
