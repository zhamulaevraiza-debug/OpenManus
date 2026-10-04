/**
 * Composer drafts per conversation ("new" for an unsaved chat), kept outside React so text and
 * in-flight uploads survive route changes and remounts.
 */
import { useCallback, useSyncExternalStore } from "react";

import { uploadFiles } from "@/api/client";

export const NEW_DRAFT = "new";

export interface Attachment {
  id: string;
  name: string;
  size: number;
  mime: string;
  status: "uploading" | "done" | "error";
  progress: number;
  /** Workspace-relative path once uploaded. */
  path: string | null;
  /** Object URL for image thumbnails. */
  previewUrl: string | null;
  error: string | null;
}

export interface Draft {
  text: string;
  attachments: Attachment[];
}

const EMPTY: Draft = { text: "", attachments: [] };
const drafts = new Map<string, Draft>();
const listeners = new Map<string, Set<() => void>>();
const aborters = new Map<string, () => void>();

function emit(key: string) {
  listeners.get(key)?.forEach((listener) => listener());
}

export function getDraft(key: string): Draft {
  return drafts.get(key) ?? EMPTY;
}

export function updateDraft(key: string, update: (draft: Draft) => Draft) {
  const next = update(getDraft(key));
  if (next.text === "" && next.attachments.length === 0) drafts.delete(key);
  else drafts.set(key, next);
  emit(key);
}

function patchAttachment(key: string, id: string, patch: Partial<Attachment>) {
  updateDraft(key, (draft) => ({
    ...draft,
    attachments: draft.attachments.map((attachment) =>
      attachment.id === id ? { ...attachment, ...patch } : attachment,
    ),
  }));
}

/** Moves a draft (e.g. from the new-chat page to the conversation just created for it). */
export function moveDraft(from: string, to: string) {
  const draft = drafts.get(from);
  if (!draft) return;
  drafts.delete(from);
  drafts.set(to, draft);
  emit(from);
  emit(to);
}

/** Empties the composer and returns what it held (to restore if sending fails). */
export function takeDraft(key: string): Draft {
  const draft = getDraft(key);
  drafts.delete(key);
  emit(key);
  return draft;
}

/** Releases a sent draft's thumbnails. */
export function releaseDraft(draft: Draft) {
  for (const attachment of draft.attachments) {
    if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  }
}

export function removeAttachment(key: string, id: string) {
  aborters.get(id)?.();
  aborters.delete(id);
  const attachment = getDraft(key).attachments.find((item) => item.id === id);
  if (attachment?.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
  updateDraft(key, (draft) => ({ ...draft, attachments: draft.attachments.filter((item) => item.id !== id) }));
  return attachment ?? null;
}

let counter = 0;

/**
 * Starts uploading files into `conversationId` (one request per file for per-chip progress).
 * `draftKey` is resolved lazily so an upload follows its draft if it moves.
 */
export function startUploads(draftKey: () => string, conversationId: string, files: File[], uploadFailed: string) {
  const created: Attachment[] = files.map((file) => ({
    id: `att-${Date.now()}-${counter++}`,
    name: file.name,
    size: file.size,
    mime: file.type,
    status: "uploading",
    progress: 0,
    path: null,
    previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
    error: null,
  }));
  updateDraft(draftKey(), (draft) => ({ ...draft, attachments: [...draft.attachments, ...created] }));

  created.forEach((attachment, index) => {
    const handle = uploadFiles(conversationId, [files[index]], {
      onProgress: (progress) => patchAttachment(draftKey(), attachment.id, { progress }),
    });
    aborters.set(attachment.id, handle.abort);
    handle.promise
      .then((entries) => {
        patchAttachment(draftKey(), attachment.id, { status: "done", progress: 1, path: entries[0]?.path ?? null });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        const message = error instanceof Error && error.message !== "network" ? error.message : uploadFailed;
        patchAttachment(draftKey(), attachment.id, { status: "error", error: message });
      })
      .finally(() => aborters.delete(attachment.id));
  });
}

export function useDraft(key: string) {
  const subscribe = useCallback(
    (listener: () => void) => {
      let set = listeners.get(key);
      if (!set) {
        set = new Set();
        listeners.set(key, set);
      }
      set.add(listener);
      return () => {
        set.delete(listener);
        if (set.size === 0) listeners.delete(key);
      };
    },
    [key],
  );
  return useSyncExternalStore(
    subscribe,
    () => getDraft(key),
    () => EMPTY,
  );
}
