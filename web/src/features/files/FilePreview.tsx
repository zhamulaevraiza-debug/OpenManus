import { useQuery } from "@tanstack/react-query";
import { FileQuestion } from "lucide-react";
import { useState, type ReactNode } from "react";

import { api, urls } from "@/api/client";
import type { FileEntry } from "@/api/types";
import { CodeView } from "@/components/CodeView";
import { Segmented } from "@/components/Form";
import { Markdown } from "@/components/Markdown";
import { Skeleton } from "@/components/Skeleton";
import { useI18n } from "@/i18n";
import { formatBytes } from "@/i18n/format";

import { codeLanguage, previewKind } from "./fileTypes";

const MAX_TEXT_PREVIEW = 1024 * 1024;

function TextContent({
  conversationId,
  entry,
  render,
}: {
  conversationId: string;
  entry: FileEntry;
  render: (text: string) => ReactNode;
}) {
  const { t } = useI18n();
  const { data, isPending, isError } = useQuery({
    queryKey: ["file-text", conversationId, entry.path, entry.modified_at, entry.size],
    queryFn: ({ signal }) => api.fileText(conversationId, entry.path, signal),
    staleTime: 60_000,
  });
  if (isPending) {
    return (
      <div className="flex flex-col gap-2 p-4" aria-busy="true">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  }
  if (isError) return <p className="p-4 text-sm text-danger">{t("files.loadFailed")}</p>;
  return <>{render(data)}</>;
}

/** Inline preview of a workspace file; HTML/SVG run in a sandboxed (opaque-origin) iframe. */
export function FilePreview({ conversationId, entry }: { conversationId: string; entry: FileEntry }) {
  const { t, language } = useI18n();
  const kind = previewKind(entry.name, entry.mime);
  const [view, setView] = useState<"rendered" | "source">("rendered");
  const src = urls.file(conversationId, entry.path);
  const textLike =
    kind === "markdown" ||
    kind === "code" ||
    kind === "text" ||
    ((kind === "html" || kind === "svg") && view === "source");

  if (textLike && entry.size > MAX_TEXT_PREVIEW) {
    return <Unavailable message={t("files.tooLarge", { size: formatBytes(entry.size, t, language) })} />;
  }

  const toggle =
    kind === "markdown" || kind === "html" || kind === "svg" ? (
      <div className="flex justify-center border-b border-border px-3 py-2">
        <Segmented
          label={entry.name}
          value={view}
          onChange={setView}
          options={[
            { value: "rendered", label: t("files.rendered") },
            { value: "source", label: t("files.source") },
          ]}
        />
      </div>
    ) : null;

  switch (kind) {
    case "image":
      return (
        <div className="flex min-h-full items-center justify-center bg-[repeating-conic-gradient(var(--surface-2)_0_25%,var(--surface)_0_50%)] bg-[length:20px_20px] p-4">
          <img src={src} alt={entry.name} className="max-h-full max-w-full rounded-lg object-contain shadow-sm" />
        </div>
      );
    case "svg":
    case "html":
      return (
        <div className="flex h-full flex-col">
          {toggle}
          {view === "rendered" ? (
            <iframe
              title={entry.name}
              src={src}
              sandbox="allow-scripts allow-popups"
              referrerPolicy="no-referrer"
              className="min-h-0 w-full flex-1 bg-white"
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <TextContent
                conversationId={conversationId}
                entry={entry}
                render={(text) => <CodeView code={text} language={kind === "svg" ? "xml" : "html"} />}
              />
            </div>
          )}
        </div>
      );
    case "pdf":
      return <iframe title={entry.name} src={src} className="h-full w-full bg-surface-2" />;
    case "audio":
      return (
        <div className="flex h-full items-center justify-center p-6">
          <audio controls src={src} className="w-full max-w-md">
            <track kind="captions" />
          </audio>
        </div>
      );
    case "video":
      return (
        <div className="flex h-full items-center justify-center bg-black p-2">
          <video controls src={src} className="max-h-full max-w-full" playsInline>
            <track kind="captions" />
          </video>
        </div>
      );
    case "markdown":
      return (
        <div className="flex h-full flex-col">
          {toggle}
          <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
            <TextContent
              conversationId={conversationId}
              entry={entry}
              render={(text) =>
                view === "rendered" ? (
                  <Markdown content={text} conversationId={conversationId} />
                ) : (
                  <CodeView code={text} language="markdown" />
                )
              }
            />
          </div>
        </div>
      );
    case "code":
    case "text":
      return (
        <div className="h-full overflow-auto p-3">
          <TextContent
            conversationId={conversationId}
            entry={entry}
            render={(text) => (
              <CodeView code={text} language={kind === "code" ? codeLanguage(entry.name) : "plaintext"} />
            )}
          />
        </div>
      );
    default:
      return <Unavailable message={t("files.previewUnavailable")} />;
  }
}

function Unavailable({ message }: { message: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <span className="inline-flex size-12 items-center justify-center rounded-2xl bg-surface-2 text-fg-subtle">
        <FileQuestion className="size-6" aria-hidden />
      </span>
      <p className="max-w-xs text-sm text-fg-muted">{message}</p>
    </div>
  );
}
