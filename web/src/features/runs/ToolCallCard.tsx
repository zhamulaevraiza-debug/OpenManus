import { Check, ChevronRight, CircleSlash, X } from "lucide-react";
import { createElement, useState } from "react";

import { CodeView } from "@/components/CodeView";
import { ImageViewer } from "@/components/ImageViewer";
import { Spinner } from "@/components/Spinner";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import type { ToolCallItem } from "./reducer";
import { codeArgument, summarizeArguments, toolIcon, toolLabel } from "./tools";

const OUTPUT_PREVIEW = 1200;

/** One tool invocation: icon, name, compact args; expands to full arguments and output. */
export function ToolCallCard({ item }: { item: ToolCallItem }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const summary = summarizeArguments(item.name, item.arguments);

  return (
    <div
      data-testid="tool-call"
      data-status={item.status}
      className={cn(
        "overflow-hidden rounded-xl border bg-surface transition-colors",
        item.status === "error" ? "border-danger/35" : "border-border",
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-10 w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-surface-2/70 max-md:min-h-11"
      >
        <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-fg-muted">
          {createElement(toolIcon(item.name), { className: "size-3.5", "aria-hidden": true })}
        </span>
        <span className="shrink-0 text-[0.8125rem] font-medium text-fg">{toolLabel(t, item.name)}</span>
        {summary && <span className="min-w-0 flex-1 truncate font-mono text-xs text-fg-subtle">{summary}</span>}
        {!summary && <span className="flex-1" />}
        <ToolStatusIcon status={item.status} />
        <ChevronRight
          className={cn("size-4 shrink-0 text-fg-subtle transition-transform duration-150", open && "rotate-90")}
          aria-hidden
        />
      </button>

      {item.imageUrl && !open && (
        <div className="px-3 pb-3">
          <Screenshot src={item.imageUrl} onOpen={() => setViewerOpen(true)} />
        </div>
      )}

      {open && (
        <div className="flex flex-col gap-3 border-t border-border bg-surface-2/40 px-3 py-3">
          <ArgumentsView name={item.name} args={item.arguments} />
          {item.status !== "running" && <OutputView item={item} />}
          {item.imageUrl && <Screenshot src={item.imageUrl} onOpen={() => setViewerOpen(true)} />}
        </div>
      )}
      <ImageViewer
        src={viewerOpen ? item.imageUrl : null}
        alt={t("activity.screenshot")}
        onClose={() => setViewerOpen(false)}
      />
    </div>
  );
}

function ToolStatusIcon({ status }: { status: ToolCallItem["status"] }) {
  const { t } = useI18n();
  if (status === "running")
    return <Spinner className="size-4 shrink-0 text-accent-text" label={t("activity.running")} />;
  if (status === "error") return <X className="size-4 shrink-0 text-danger" aria-label={t("activity.error")} />;
  if (status === "interrupted")
    return <CircleSlash className="size-4 shrink-0 text-fg-subtle" aria-label={t("activity.interrupted")} />;
  return <Check className="size-4 shrink-0 text-success" aria-hidden />;
}

function ArgumentsView({ name, args }: { name: string; args: Record<string, unknown> }) {
  const { t } = useI18n();
  if (Object.keys(args).length === 0) return null;
  const code = codeArgument(name, args);
  const rest = code
    ? Object.fromEntries(Object.entries(args).filter(([key]) => key !== "code" && key !== "command"))
    : args;
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-fg-subtle">{t("activity.arguments")}</p>
      {code && <CodeView code={code.code} language={code.language} />}
      {Object.keys(rest).length > 0 && <CodeView code={JSON.stringify(rest, null, 2)} language="json" />}
    </div>
  );
}

function OutputView({ item }: { item: ToolCallItem }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const output = item.output ?? "";
  const long = output.length > OUTPUT_PREVIEW;
  const shown = expanded || !long ? output : `${output.slice(0, OUTPUT_PREVIEW)}…`;
  return (
    <div className="flex flex-col gap-1.5">
      <p className={cn("text-xs font-medium", item.status === "error" ? "text-danger" : "text-fg-subtle")}>
        {item.status === "error" ? t("activity.error") : t("activity.result")}
      </p>
      {output ? (
        <pre
          className={cn(
            "max-h-96 overflow-auto rounded-lg border bg-code-bg px-3 py-2.5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap",
            item.status === "error" ? "border-danger/30 text-danger" : "border-border text-fg-muted",
          )}
        >
          {shown}
        </pre>
      ) : (
        <p className="text-xs text-fg-subtle italic">{t("activity.noOutput")}</p>
      )}
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="self-start text-xs font-medium text-accent-text hover:underline"
        >
          {expanded ? t("common.showLess") : t("common.showMore")}
        </button>
      )}
    </div>
  );
}

function Screenshot({ src, onOpen }: { src: string; onOpen: () => void }) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={t("activity.openScreenshot")}
      className="group block overflow-hidden rounded-lg border border-border bg-surface-2"
    >
      <img
        src={src}
        alt={t("activity.screenshot")}
        loading="lazy"
        className="max-h-56 w-auto max-w-full object-contain transition-transform duration-200 group-hover:scale-[1.01]"
      />
    </button>
  );
}
