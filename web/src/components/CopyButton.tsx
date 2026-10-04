import { Check, Copy } from "lucide-react";

import { useCopy } from "@/hooks/useCopy";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

interface CopyButtonProps {
  text: string | (() => string);
  label?: string;
  showText?: boolean;
  className?: string;
}

export function CopyButton({ text, label, showText = false, className }: CopyButtonProps) {
  const { t } = useI18n();
  const { copied, copy } = useCopy();
  const name = copied ? t("common.copied") : (label ?? t("common.copy"));
  return (
    <button
      type="button"
      onClick={() => copy(typeof text === "function" ? text() : text)}
      aria-label={name}
      title={name}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg text-fg-subtle transition-colors hover:bg-surface-3 hover:text-fg [&_svg]:size-4",
        showText ? "h-8 px-2 text-xs font-medium" : "size-8 max-md:size-10",
        className,
      )}
    >
      {copied ? <Check className="text-success" /> : <Copy />}
      {showText && <span aria-live="polite">{copied ? t("common.copied") : t("common.copy")}</span>}
    </button>
  );
}
