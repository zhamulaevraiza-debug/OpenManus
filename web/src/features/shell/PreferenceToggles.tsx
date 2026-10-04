import { Languages, Monitor, Moon, Sun } from "lucide-react";

import { IconButton } from "@/components/Button";
import { useI18n } from "@/i18n";
import { useTheme, type ThemePreference } from "@/theme";

const NEXT_THEME: Record<ThemePreference, ThemePreference> = { system: "light", light: "dark", dark: "system" };
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon };

/** Compact language + theme switches (used where the settings page isn't reachable, e.g. sign-in). */
export function PreferenceToggles() {
  const { t, language, setLanguage } = useI18n();
  const { preference, setPreference } = useTheme();
  const ThemeIcon = THEME_ICON[preference];
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => setLanguage(language === "ru" ? "en" : "ru")}
        aria-label={t("settings.appearance.language")}
        title={t("settings.appearance.language")}
        className="inline-flex h-10 items-center gap-1.5 rounded-xl px-3 text-sm font-medium text-fg-muted hover:bg-surface-2 hover:text-fg max-md:h-11"
      >
        <Languages className="size-4" aria-hidden />
        {language === "ru" ? "RU" : "EN"}
      </button>
      <IconButton label={t("theme.toggle")} onClick={() => setPreference(NEXT_THEME[preference])}>
        <ThemeIcon />
      </IconButton>
    </div>
  );
}
