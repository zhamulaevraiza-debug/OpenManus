import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { useMediaQuery } from "@/hooks/useMediaQuery";

import {
  applyTheme,
  readThemePreference,
  THEME_STORAGE_KEY,
  ThemeContext,
  type ThemePreference,
  type ThemeValue,
} from "./index";

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readThemePreference);
  const systemDark = useMediaQuery("(prefers-color-scheme: dark)");
  const resolved = preference === "system" ? (systemDark ? "dark" : "light") : preference;

  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // storage unavailable: the choice lasts for this session only
    }
  }, []);

  const value = useMemo<ThemeValue>(
    () => ({ preference, resolved, setPreference }),
    [preference, resolved, setPreference],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
