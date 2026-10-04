import { Download, ExternalLink, Monitor, Share, Smartphone } from "lucide-react";
import { useSyncExternalStore } from "react";

import { useHealth, useStatus } from "@/api/queries";
import { Button } from "@/components/Button";
import { LogoMark } from "@/components/Logo";
import { Skeleton } from "@/components/Skeleton";
import { useI18n } from "@/i18n";
import { canPromptInstall, promptInstall, subscribeInstallPrompt } from "@/pwa";
import { cn } from "@/utils/cn";

import { Section } from "./Section";

export function AboutTab() {
  const { t } = useI18n();
  const health = useHealth();
  const { data: status } = useStatus();
  const installable = useSyncExternalStore(subscribeInstallPrompt, canPromptInstall, () => false);
  const online = health.data?.status === "ok";

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("settings.tabs.about")}>
        <div className="flex items-center gap-4">
          <LogoMark className="size-12" />
          <div>
            <p className="text-base font-semibold text-fg">{t("common.appName")}</p>
            <p className="text-sm text-fg-muted">{t("auth.tagline")}</p>
          </div>
        </div>
        <dl className="mt-5 grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
          <div className="bg-surface px-4 py-3">
            <dt className="text-xs text-fg-subtle">{t("settings.about.version")}</dt>
            <dd className="mt-0.5 font-mono text-sm text-fg">
              {health.isPending ? <Skeleton className="h-5 w-16" /> : (health.data?.version ?? status?.version ?? "—")}
            </dd>
          </div>
          <div className="bg-surface px-4 py-3">
            <dt className="text-xs text-fg-subtle">{t("settings.about.server")}</dt>
            <dd className="mt-0.5 flex items-center gap-2 text-sm text-fg">
              <span className={cn("size-2 rounded-full", online ? "bg-success" : "bg-danger")} aria-hidden />
              {health.isPending ? (
                <Skeleton className="h-5 w-16" />
              ) : online ? (
                t("settings.about.online")
              ) : (
                t("settings.about.offline")
              )}
            </dd>
          </div>
          <div className="bg-surface px-4 py-3">
            <dt className="text-xs text-fg-subtle">{t("settings.about.activeRuns")}</dt>
            <dd className="mt-0.5 text-sm text-fg tabular-nums">
              {status ? `${status.active_runs} / ${status.max_concurrent_runs}` : "—"}
            </dd>
          </div>
        </dl>
        <a
          href="https://github.com/FoundationAgents/OpenManus"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-accent-text hover:underline"
        >
          {t("settings.about.sourceCode")}
          <ExternalLink className="size-3.5" aria-hidden />
        </a>
      </Section>
      <Section
        title={t("settings.about.installTitle")}
        description={t("settings.about.installHint")}
        actions={
          installable ? (
            <Button
              variant="primary"
              size="sm"
              icon={<Download className="size-4" />}
              onClick={() => void promptInstall()}
            >
              {t("settings.about.installButton")}
            </Button>
          ) : null
        }
      >
        <ul className="flex flex-col gap-3 text-sm text-fg-muted">
          <li className="flex items-start gap-3">
            <Smartphone className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden />
            {t("settings.about.installAndroid")}
          </li>
          <li className="flex items-start gap-3">
            <Share className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden />
            {t("settings.about.installIos")}
          </li>
          <li className="flex items-start gap-3">
            <Monitor className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden />
            {t("settings.about.installDesktop")}
          </li>
        </ul>
      </Section>
    </div>
  );
}
