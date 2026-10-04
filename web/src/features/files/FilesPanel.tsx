import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FolderArchive,
  FolderOpen,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api, uploadFiles, urls } from "@/api/client";
import { queryKeys, useFiles } from "@/api/queries";
import type { FileEntry } from "@/api/types";
import { IconButton } from "@/components/Button";
import { ConfirmDialog } from "@/components/Modal";
import { Skeleton } from "@/components/Skeleton";
import { Spinner } from "@/components/Spinner";
import { useToast } from "@/components/toast";
import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useIsWide } from "@/hooks/useMediaQuery";
import { useI18n } from "@/i18n";
import { formatBytes, formatShortDate } from "@/i18n/format";
import { cn } from "@/utils/cn";
import { errorMessage } from "@/utils/errors";

import { FileIcon } from "./FileIcon";
import { FilePreview } from "./FilePreview";
import { buildFileTree, treeSize, type TreeNode } from "./tree";

interface FilesPanelProps {
  conversationId: string | null;
  selectedPath: string | null;
  onSelect: (path: string | null) => void;
  onClose: () => void;
}

/** Workspace files: docked right panel from 1024px, full-screen sheet below. */
export function FilesPanel(props: FilesPanelProps) {
  const wide = useIsWide();
  if (wide) {
    return (
      <aside className="flex w-[24rem] shrink-0 flex-col border-l border-border bg-surface xl:w-[28rem]">
        <PanelContent {...props} />
      </aside>
    );
  }
  return createPortal(<Sheet {...props} />, document.body);
}

function Sheet(props: FilesPanelProps) {
  useBodyScrollLock(true);
  return (
    <div
      className="pt-safe pb-safe fixed inset-0 z-40 flex animate-slide-up flex-col bg-surface"
      role="dialog"
      aria-modal="true"
    >
      <PanelContent {...props} />
    </div>
  );
}

function PanelContent({ conversationId, selectedPath, onSelect, onClose }: FilesPanelProps) {
  const { t, language } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const uploadRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState<number | null>(null);
  const [toDelete, setToDelete] = useState<FileEntry | null>(null);
  const { data, isPending, isError, isFetching, refetch } = useFiles(conversationId ?? undefined);
  const files = useMemo(() => data ?? [], [data]);
  const tree = useMemo(() => buildFileTree(files), [files]);
  const fileCount = files.filter((entry) => !entry.is_dir).length;
  const selected = selectedPath ? (files.find((entry) => entry.path === selectedPath && !entry.is_dir) ?? null) : null;

  const upload = async (list: File[]) => {
    if (!conversationId || list.length === 0) return;
    setUploading(0);
    try {
      const handle = uploadFiles(conversationId, list, { onProgress: (fraction) => setUploading(fraction) });
      const uploaded = await handle.promise;
      toast.success(t("files.uploaded", { count: uploaded.length }));
    } catch (error) {
      toast.error(errorMessage(t, error, "chat.uploadFailed"));
    } finally {
      setUploading(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.files(conversationId) });
    }
  };

  const remove = async (entry: FileEntry) => {
    if (!conversationId) return;
    try {
      await api.deleteFile(conversationId, entry.path);
    } catch (error) {
      toast.error(errorMessage(t, error));
      throw error;
    }
    if (selectedPath === entry.path) onSelect(null);
    toast.success(t("files.deleted"));
    void queryClient.invalidateQueries({ queryKey: queryKeys.files(conversationId) });
  };

  return (
    <div data-testid="files-panel" className="flex h-full min-h-0 flex-col" aria-label={t("files.title")} role="region">
      {selected && conversationId ? (
        <>
          <div className="flex h-14 shrink-0 items-center gap-1 border-b border-border px-2">
            <IconButton label={t("files.backToList")} onClick={() => onSelect(null)}>
              <ArrowLeft />
            </IconButton>
            <div className="min-w-0 flex-1 px-1">
              <p className="truncate text-sm font-semibold text-fg">{selected.name}</p>
              <p className="truncate text-xs text-fg-subtle">
                {formatBytes(selected.size, t, language)} · {formatShortDate(selected.modified_at, language)}
              </p>
            </div>
            <a
              href={urls.file(conversationId, selected.path)}
              target="_blank"
              rel="noopener"
              aria-label={t("files.openInNewTab")}
              title={t("files.openInNewTab")}
              className="inline-flex size-10 items-center justify-center rounded-xl text-fg-muted hover:bg-surface-2 hover:text-fg max-md:size-11"
            >
              <ExternalLink className="size-[18px]" />
            </a>
            <a
              href={urls.file(conversationId, selected.path, true)}
              download={selected.name}
              aria-label={t("files.download")}
              title={t("files.download")}
              className="inline-flex size-10 items-center justify-center rounded-xl text-fg-muted hover:bg-surface-2 hover:text-fg max-md:size-11"
            >
              <Download className="size-[18px]" />
            </a>
            <IconButton label={t("files.delete")} onClick={() => setToDelete(selected)}>
              <Trash2 />
            </IconButton>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <FilePreview conversationId={conversationId} entry={selected} />
          </div>
        </>
      ) : (
        <>
          <div className="flex h-14 shrink-0 items-center gap-1 border-b border-border pr-2 pl-4">
            <div className="flex min-w-0 flex-1 items-baseline gap-2">
              <h2 className="text-[0.9375rem] font-semibold text-fg">{t("files.title")}</h2>
              {fileCount > 0 && (
                <span className="text-xs text-fg-subtle">{t("files.count", { count: fileCount })}</span>
              )}
            </div>
            {conversationId && (
              <>
                <IconButton label={t("common.refresh")} onClick={() => refetch()} disabled={isFetching}>
                  <RefreshCw className={cn(isFetching && "animate-spin")} />
                </IconButton>
                <IconButton
                  label={t("files.upload")}
                  onClick={() => uploadRef.current?.click()}
                  disabled={uploading !== null}
                >
                  {uploading !== null ? <Spinner /> : <Upload />}
                </IconButton>
                {fileCount > 0 && (
                  <a
                    href={urls.filesZip(conversationId)}
                    download
                    aria-label={t("files.downloadAll")}
                    title={t("files.downloadAll")}
                    className="inline-flex size-10 items-center justify-center rounded-xl text-fg-muted hover:bg-surface-2 hover:text-fg max-md:size-11"
                  >
                    <FolderArchive className="size-[18px]" />
                  </a>
                )}
              </>
            )}
            <IconButton label={t("files.close")} onClick={onClose}>
              <X />
            </IconButton>
          </div>
          {uploading !== null && (
            <div
              className="h-0.5 shrink-0 bg-surface-3"
              role="progressbar"
              aria-valuenow={Math.round(uploading * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full bg-accent transition-[width]"
                style={{ width: `${Math.round(uploading * 100)}%` }}
              />
            </div>
          )}
          <div className="scroll-area min-h-0 flex-1 overflow-y-auto px-2 py-2">
            {!conversationId ? (
              <EmptyFiles title={t("files.empty")} hint={t("files.noChat")} />
            ) : isPending ? (
              <div className="flex flex-col gap-2 p-2" aria-busy="true">
                {Array.from({ length: 6 }, (_, index) => (
                  <Skeleton key={index} className="h-9 w-full" />
                ))}
              </div>
            ) : isError ? (
              <div className="p-4 text-sm text-fg-muted">
                <p>{t("files.loadFailed")}</p>
                <button
                  type="button"
                  onClick={() => refetch()}
                  className="mt-2 font-medium text-accent-text hover:underline"
                >
                  {t("common.retry")}
                </button>
              </div>
            ) : tree.length === 0 ? (
              <EmptyFiles title={t("files.empty")} hint={t("files.emptyHint")} />
            ) : (
              <ul role="tree" aria-label={t("files.title")} className="flex flex-col">
                {tree.map((node) => (
                  <TreeItem
                    key={node.entry.path}
                    node={node}
                    depth={0}
                    conversationId={conversationId}
                    defaultOpen={files.length <= 60}
                    onSelect={onSelect}
                    onDelete={setToDelete}
                  />
                ))}
              </ul>
            )}
          </div>
        </>
      )}
      <input
        ref={uploadRef}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          void upload(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      <ConfirmDialog
        open={toDelete !== null}
        title={t("files.deleteTitle")}
        body={toDelete ? t("files.deleteBody", { name: toDelete.name }) : undefined}
        confirmLabel={t("common.delete")}
        danger
        onConfirm={() => (toDelete ? remove(toDelete) : undefined)}
        onClose={() => setToDelete(null)}
      />
    </div>
  );
}

function EmptyFiles({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center px-6 pt-16 text-center">
      <span className="inline-flex size-14 items-center justify-center rounded-2xl bg-accent-soft text-accent-text">
        <FolderOpen className="size-7" aria-hidden />
      </span>
      <p className="mt-4 text-sm font-semibold text-fg">{title}</p>
      <p className="mt-1 max-w-64 text-sm leading-relaxed text-fg-subtle">{hint}</p>
    </div>
  );
}

interface TreeItemProps {
  node: TreeNode;
  depth: number;
  conversationId: string;
  defaultOpen: boolean;
  onSelect: (path: string) => void;
  onDelete: (entry: FileEntry) => void;
}

function TreeItem({ node, depth, conversationId, defaultOpen, onSelect, onDelete }: TreeItemProps) {
  const { t, language } = useI18n();
  const [open, setOpen] = useState(defaultOpen || depth === 0);
  const { entry } = node;
  const indent = { paddingLeft: `${0.5 + depth}rem` };

  if (entry.is_dir) {
    return (
      <li role="treeitem" aria-expanded={open} aria-selected={false}>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-label={open ? t("files.collapse", { name: entry.name }) : t("files.expand", { name: entry.name })}
          className="flex h-9 w-full items-center gap-2 rounded-lg pr-3 text-left text-sm text-fg hover:bg-surface-2 max-md:h-11"
          style={indent}
        >
          <ChevronRight
            className={cn("size-3.5 shrink-0 text-fg-subtle transition-transform", open && "rotate-90")}
            aria-hidden
          />
          <FileIcon name={entry.name} isDir className="size-4 shrink-0 text-accent-text" />
          <span className="min-w-0 flex-1 truncate font-medium">{entry.name}</span>
          <span className="shrink-0 text-xs text-fg-subtle tabular-nums">
            {formatBytes(treeSize(node), t, language)}
          </span>
        </button>
        {open && node.children.length > 0 && (
          <ul role="group" className="flex flex-col">
            {node.children.map((child) => (
              <TreeItem
                key={child.entry.path}
                node={child}
                depth={depth + 1}
                conversationId={conversationId}
                defaultOpen={defaultOpen}
                onSelect={onSelect}
                onDelete={onDelete}
              />
            ))}
          </ul>
        )}
      </li>
    );
  }

  return (
    <li role="treeitem" aria-selected={false} className="group relative">
      <button
        type="button"
        data-testid="file-row"
        onClick={() => onSelect(entry.path)}
        className="flex h-11 w-full items-center gap-2.5 rounded-lg pr-20 text-left hover:bg-surface-2 max-md:h-12"
        style={{ paddingLeft: `${1.875 + depth}rem` }}
      >
        <FileIcon name={entry.name} mime={entry.mime} className="size-4 shrink-0 text-fg-subtle" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-fg">{entry.name}</span>
          <span className="block truncate text-[0.6875rem] text-fg-subtle tabular-nums">
            {formatBytes(entry.size, t, language)} · {formatShortDate(entry.modified_at, language)}
          </span>
        </span>
      </button>
      <div className="absolute top-1/2 right-1 flex -translate-y-1/2 items-center gap-0.5 transition-opacity [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:focus-within:opacity-100">
        <a
          href={urls.file(conversationId, entry.path, true)}
          download={entry.name}
          aria-label={`${t("files.download")} ${entry.name}`}
          title={t("files.download")}
          className="inline-flex size-8 items-center justify-center rounded-lg text-fg-subtle hover:bg-surface-3 hover:text-fg max-md:size-10"
        >
          <Download className="size-4" />
        </a>
        <button
          type="button"
          onClick={() => onDelete(entry)}
          aria-label={`${t("files.delete")} ${entry.name}`}
          title={t("files.delete")}
          className="inline-flex size-8 items-center justify-center rounded-lg text-fg-subtle hover:bg-danger-soft hover:text-danger max-md:size-10"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </li>
  );
}
