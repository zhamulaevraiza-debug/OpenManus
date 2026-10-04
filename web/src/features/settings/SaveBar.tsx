import { Button } from "@/components/Button";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

interface SaveBarProps {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}

/** Sticky footer that appears while a settings form has unsaved changes. */
export function SaveBar({ dirty, saving, onSave, onDiscard }: SaveBarProps) {
  const { t } = useI18n();
  return (
    <div
      aria-hidden={!dirty}
      className={cn(
        "pb-safe sticky bottom-0 z-10 -mx-4 mt-4 px-4 pb-4 transition-all duration-200 md:-mx-8 md:px-8",
        dirty ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-4 opacity-0",
      )}
    >
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-surface/95 py-2.5 pr-2.5 pl-4 shadow-lg backdrop-blur">
        <p className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{t("settings.unsaved")}</p>
        <Button variant="ghost" size="sm" onClick={onDiscard} disabled={saving} tabIndex={dirty ? 0 : -1}>
          {t("common.discard")}
        </Button>
        <Button variant="primary" size="sm" onClick={onSave} loading={saving} tabIndex={dirty ? 0 : -1}>
          {t("common.save")}
        </Button>
      </div>
    </div>
  );
}
