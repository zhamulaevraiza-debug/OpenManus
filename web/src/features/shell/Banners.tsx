import { CloudOff, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";

import { useMe, useStatus } from "@/api/queries";
import { useOnline } from "@/hooks/useOnline";
import { useI18n } from "@/i18n";

/** Offline notice and the "model not configured" warning (admins get a link to fix it). */
export function Banners() {
  const { t } = useI18n();
  const online = useOnline();
  const { data: me } = useMe();
  const { data: status } = useStatus(Boolean(me));
  const needsModel = status && !status.llm_configured;
  if (online && !needsModel) return null;
  return (
    <div className="shrink-0">
      {!online && (
        <div
          role="status"
          className="flex items-center gap-2 border-b border-border bg-surface-2 px-4 py-2 text-[0.8125rem] text-fg-muted md:text-sm"
        >
          <CloudOff className="size-4 shrink-0" aria-hidden />
          {t("banner.offline")}
        </div>
      )}
      {needsModel && (
        <div
          role="alert"
          className="flex items-start gap-2.5 border-b border-warning/20 bg-warning-soft px-4 py-2 text-[0.8125rem] leading-relaxed text-fg md:items-center md:text-sm"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning md:mt-0" aria-hidden />
          <p className="min-w-0 flex-1">
            {status.is_admin ? t("banner.llmNotConfiguredAdmin") : t("banner.llmNotConfiguredUser")}{" "}
            {status.is_admin && (
              <Link
                to="/settings/model"
                className="font-semibold whitespace-nowrap text-accent-text underline-offset-2 hover:underline"
              >
                {t("banner.configureModel")} →
              </Link>
            )}
          </p>
        </div>
      )}
    </div>
  );
}
