import { ArrowUp, Camera, CircleAlert, FileText, Mic, Paperclip, Square, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useNavigate } from "react-router-dom";

import type { AgentInfo, Mode, Run } from "@/api/types";
import { IconButton } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { useToast } from "@/components/toast";
import { useIsTouch } from "@/hooks/useMediaQuery";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { useI18n } from "@/i18n";
import { formatBytes } from "@/i18n/format";
import { cn } from "@/utils/cn";
import { errorMessage } from "@/utils/errors";

import { NEW_DRAFT, removeAttachment, startUploads, takeDraft, updateDraft, useDraft, type Attachment } from "./drafts";
import { ModePicker } from "./ModePicker";
import { useChatActions } from "./useChatActions";

const MAX_LENGTH = 20000;

interface ComposerProps {
  conversationId: string | null;
  mode: string;
  onModeChange: (mode: string) => void;
  modes: readonly Mode[] | undefined;
  agents: readonly AgentInfo[] | undefined;
  activeRun: Run | null;
  onStop: () => void;
}

export function Composer({ conversationId, mode, onModeChange, modes, agents, activeRun, onStop }: ComposerProps) {
  const { t, language } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  const isTouch = useIsTouch();
  const { send, createConversation } = useChatActions();
  const draftKey = conversationId ?? NEW_DRAFT;
  const draft = useDraft(draftKey);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const dictationBase = useRef("");

  const setText = useCallback((text: string) => updateDraft(draftKey, (current) => ({ ...current, text })), [draftKey]);

  const speech = useSpeechRecognition({
    lang: language === "ru" ? "ru-RU" : "en-US",
    onTranscript: (transcript) => setText([dictationBase.current, transcript].filter(Boolean).join(" ")),
    onError: () => toast.error(t("chat.dictationError")),
  });

  // Auto-grow the textarea with its content (capped by CSS max-height).
  useLayoutEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [draft.text]);

  // Focus the composer when switching chats on keyboard devices (not on phones: no surprise keyboard).
  useEffect(() => {
    if (!isTouch) textareaRef.current?.focus({ preventScroll: true });
  }, [conversationId, isTouch]);

  const uploading = draft.attachments.some((attachment) => attachment.status === "uploading");
  const ready = draft.attachments.filter((attachment) => attachment.status === "done" && attachment.path);
  const canSend = !activeRun && !uploading && (draft.text.trim().length > 0 || ready.length > 0);

  const addFiles = async (files: File[]) => {
    if (files.length === 0) return;
    let id = conversationId;
    if (!id) {
      try {
        id = (await createConversation(mode)).id;
        navigate(`/c/${id}`);
      } catch (error) {
        toast.error(errorMessage(t, error, "chat.uploadFailed"));
        return;
      }
    }
    const target = id;
    startUploads(() => target, target, files, t("chat.uploadFailed"));
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (activeRun) return;
    if (uploading) {
      toast.info(t("chat.waitForUploads"));
      return;
    }
    if (!canSend) return;
    if (speech.listening) speech.stop();
    const text = draft.text.trim();
    const snapshot = takeDraft(draftKey);
    void send({
      conversationId,
      content: text || ready.map((attachment) => attachment.name).join(", "),
      mode,
      attachments: ready.map((attachment) => attachment.path as string),
      snapshot,
    });
    if (!isTouch) textareaRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || isTouch) return;
    event.preventDefault();
    submit();
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files);
    if (files.length > 0) {
      event.preventDefault();
      void addFiles(files);
    }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void addFiles(Array.from(event.dataTransfer.files));
  };

  const toggleDictation = () => {
    if (speech.listening) {
      speech.stop();
      return;
    }
    dictationBase.current = draft.text.trim();
    speech.start();
  };

  return (
    <form
      onSubmit={submit}
      aria-label={t("chat.composer")}
      className="pb-safe mx-auto w-full max-w-3xl shrink-0 px-3 pt-1 pb-3 md:px-6 md:pb-4"
    >
      <div
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes("Files")) {
            event.preventDefault();
            setDragging(true);
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "rounded-[1.75rem] border bg-surface shadow-md transition-[border-color,box-shadow] duration-150 focus-within:border-border-strong focus-within:shadow-lg",
          dragging ? "border-accent ring-4 ring-accent/15" : "border-border",
        )}
      >
        {draft.attachments.length > 0 && (
          <ul aria-label={t("chat.attachments")} className="flex gap-2 overflow-x-auto px-3 pt-3 pb-1">
            {draft.attachments.map((attachment) => (
              <AttachmentChip
                key={attachment.id}
                attachment={attachment}
                onRemove={() => removeAttachment(draftKey, attachment.id)}
              />
            ))}
          </ul>
        )}
        <textarea
          ref={textareaRef}
          data-testid="composer-input"
          rows={1}
          value={draft.text}
          maxLength={MAX_LENGTH}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={activeRun ? t("chat.placeholderRunning") : t("chat.placeholder")}
          aria-label={t("chat.placeholder")}
          enterKeyHint={isTouch ? "enter" : "send"}
          className="block max-h-[min(40vh,18rem)] min-h-[3.25rem] w-full resize-none overflow-y-auto bg-transparent px-4 pt-3.5 pb-1.5 text-base leading-relaxed text-fg placeholder:text-fg-subtle focus:outline-none md:text-[0.9375rem]"
        />
        <div className="flex min-w-0 items-center gap-1 px-2 pb-2">
          <IconButton label={t("chat.attach")} onClick={() => fileInputRef.current?.click()}>
            <Paperclip />
          </IconButton>
          {isTouch && (
            <IconButton label={t("chat.camera")} onClick={() => cameraInputRef.current?.click()}>
              <Camera />
            </IconButton>
          )}
          <ModePicker value={mode} onChange={onModeChange} modes={modes} agents={agents} />
          <div className="flex-1" />
          {speech.supported && (
            <IconButton
              label={speech.listening ? t("chat.stopDictation") : t("chat.dictate")}
              aria-pressed={speech.listening}
              onClick={toggleDictation}
              className={cn(speech.listening && "bg-danger-soft text-danger hover:bg-danger-soft hover:text-danger")}
            >
              <Mic className={cn(speech.listening && "animate-pulse")} />
            </IconButton>
          )}
          {activeRun ? (
            <IconButton
              label={t("chat.stop")}
              data-testid="stop-run"
              variant="inverse"
              onClick={onStop}
              className="rounded-full"
            >
              <Square className="!size-3.5 fill-current" />
            </IconButton>
          ) : (
            <IconButton
              type="submit"
              label={uploading ? t("chat.waitForUploads") : t("chat.send")}
              data-testid="composer-send"
              variant="primary"
              disabled={!canSend}
              className="rounded-full disabled:bg-surface-3 disabled:text-fg-subtle disabled:opacity-100 disabled:shadow-none"
            >
              {uploading ? <Spinner /> : <ArrowUp className="!size-5" strokeWidth={2.25} />}
            </IconButton>
          )}
        </div>
      </div>
      <p className="mt-2 hidden text-center text-xs text-fg-subtle md:block">{t("chat.enterHint")}</p>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          void addFiles(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        onChange={(event) => {
          void addFiles(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />
    </form>
  );
}

function AttachmentChip({ attachment, onRemove }: { attachment: Attachment; onRemove: () => void }) {
  const { t, language } = useI18n();
  const percent = Math.round(attachment.progress * 100);
  const failed = attachment.status === "error";
  return (
    <li
      className={cn(
        "relative flex h-14 w-52 shrink-0 items-center gap-2.5 overflow-hidden rounded-2xl border bg-surface-2 py-2 pr-9 pl-2",
        failed ? "border-danger/50" : "border-border",
      )}
      title={attachment.error ?? attachment.name}
    >
      {attachment.previewUrl ? (
        <img src={attachment.previewUrl} alt="" className="size-10 shrink-0 rounded-lg object-cover" />
      ) : (
        <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
          {failed ? <CircleAlert className="size-5 text-danger" /> : <FileText className="size-5" />}
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-fg">{attachment.name}</span>
        <span className={cn("block truncate text-xs", failed ? "text-danger" : "text-fg-subtle")}>
          {failed
            ? (attachment.error ?? t("chat.uploadFailed"))
            : attachment.status === "uploading"
              ? t("chat.uploading", { percent })
              : formatBytes(attachment.size, t, language)}
        </span>
      </span>
      {attachment.status === "uploading" && (
        <span
          className="absolute inset-x-0 bottom-0 h-0.5 bg-accent transition-[width] duration-200"
          style={{ width: `${percent}%` }}
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={attachment.name}
        />
      )}
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("chat.removeAttachment", { name: attachment.name })}
        className="absolute top-1/2 right-1.5 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-full text-fg-subtle hover:bg-surface-3 hover:text-fg"
      >
        <X className="size-3.5" />
      </button>
    </li>
  );
}
