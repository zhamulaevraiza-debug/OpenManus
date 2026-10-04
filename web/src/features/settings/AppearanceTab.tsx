import { Bell, BellOff, Monitor, Moon, Sun } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/Button";
import { Segmented } from "@/components/Form";
import { LANGUAGE_NAMES, LANGUAGES, useI18n, type Language } from "@/i18n";
import { useTheme, type ThemePreference } from "@/theme";
import { notificationsSupported, requestNotificationPermission } from "@/utils/notify";

import { Section } from "./Section";

function currentPermission(): NotificationPermission | "unsupported" {
  return notificationsSupported() ? Notification.permission : "unsupported";
}

export function AppearanceTab() {
  const { t, language, setLanguage } = useI18n();
  const { preference, setPreference } = useTheme();
  const [permission, setPermission] = useState(currentPermission);

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("settings.appearance.language")} description={t("settings.appearance.languageHint")}>
        <Segmented<Language>
          label={t("settings.appearance.language")}
          value={language}
          onChange={setLanguage}
          options={LANGUAGES.map((code) => ({ value: code, label: LANGUAGE_NAMES[code] }))}
          className="w-full max-w-sm"
        />
      </Section>
      <Section title={t("settings.appearance.theme")}>
        <Segmented<ThemePreference>
          label={t("settings.appearance.theme")}
          value={preference}
          onChange={setPreference}
          options={[
            { value: "light", label: t("settings.appearance.light"), icon: <Sun /> },
            { value: "dark", label: t("settings.appearance.dark"), icon: <Moon /> },
            { value: "system", label: t("settings.appearance.system"), icon: <Monitor /> },
          ]}
          className="w-full max-w-md"
        />
      </Section>
      <Section title={t("settings.appearance.notifications")} description={t("settings.appearance.notificationsHint")}>
        {permission === "granted" ? (
          <p className="flex items-center gap-2 text-sm font-medium text-success">
            <Bell className="size-4" aria-hidden />
            {t("settings.appearance.notificationsOn")}
          </p>
        ) : permission === "denied" ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted">
            <BellOff className="size-4" aria-hidden />
            {t("settings.appearance.notificationsBlocked")}
          </p>
        ) : permission === "unsupported" ? (
          <p className="text-sm text-fg-muted">{t("settings.appearance.notificationsUnsupported")}</p>
        ) : (
          <Button
            variant="outline"
            icon={<Bell className="size-4" />}
            onClick={async () => setPermission(await requestNotificationPermission())}
          >
            {t("settings.appearance.enableNotifications")}
          </Button>
        )}
      </Section>
    </div>
  );
}
