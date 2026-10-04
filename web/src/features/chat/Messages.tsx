import { Ban, CircleX, RotateCcw } from "lucide-react";
import { useState } from "react";

import { urls } from "@/api/client";
import type { Message, Run } from "@/api/types";
import { Button } from "@/components/Button";
import { CopyButton } from "@/components/CopyButton";
import { LogoMark } from "@/components/Logo";
import { Markdown } from "@/components/Markdown";
import { useFilesPanel } from "@/features/files/panel";
import { FileIcon } from "@/features/files/FileIcon";
import { previewKind } from "@/features/files/fileTypes";
import { RunActivity } from "@/features/runs/ActivityTimeline";
import { AskHumanCard } from "@/features/runs/AskHumanCard";
import { isTerminalStatus } from "@/features/runs/reducer";
import { useRunView } from "@/features/runs/useRunView";
import { useI18n } from "@/i18n";
import { formatDuration, formatNumber } from "@/i18n/format";
import { cn } from "@/utils/cn";

import { useChatActions } from "./useChatActions";

export function UserMessage({ message }: { message: Message }) {
  const { t } = useI18n();
  const pending = message.id.startsWith("local-");
  return (
    <div className="group flex flex-col items-end gap-1.5">
      {message.attachments.length > 0 && (
        <ul aria-label={t("chat.attachments")} className="flex max-w-[85%] flex-wrap justify-end gap-2">
          {message.attachments.map((path) => (
            <AttachmentLink key={path} conversationId={message.conversation_id} path={path} />
          ))}
        </ul>
      )}
      <div className="flex w-full items-center justify-end gap-1.5">
        <div className="opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 max-md:hidden">
          <CopyButton text={message.content} label={t("chat.copyMessage")} />
        </div>
        <div
          data-testid="message-user"
          className={cn(
            "max-w-[85%] rounded-3xl rounded-br-lg bg-surface-2 px-4 py-2.5 text-[0.9375rem] leading-relaxed break-words whitespace-pre-wrap text-fg sm:max-w-[75%] dark:bg-surface-3",
            pending && "opacity-70",
          )}
        >
          {message.content}
        </div>
      </div>
    </div>
  );
}

function AttachmentLink({ conversationId, path }: { conversationId: string; path: string }) {
  const panel = useFilesPanel();
  const name = path.split("/").pop() ?? path;
  const kind = previewKind(name, null);
  const open = () => panel?.show(path);
  if (kind === "image") {
    return (
      <li>
        <button
          type="button"
          onClick={open}
          title={name}
          className="block overflow-hidden rounded-2xl border border-border"
        >
          <img
            src={urls.file(conversationId, path)}
            alt={name}
            loading="lazy"
            className="size-24 object-cover sm:size-28"
          />
        </button>
      </li>
    );
  }
  return (
    <li>
      <button
        type="button"
        onClick={open}
        className="flex h-11 max-w-60 items-center gap-2 rounded-2xl border border-border bg-surface px-3 text-sm text-fg hover:bg-surface-2"
      >
        <FileIcon name={name} className="size-4 shrink-0 text-accent-text" />
        <span className="truncate">{name}</span>
      </button>
    </li>
  );
}

interface RunEntryProps {
  run: Run;
  message: Message | null;
  conversationId: string;
  isLast: boolean;
}

/** One agent run: activity timeline, question card, the (streaming) answer, retry and usage. */
export function RunEntry({ run, message, conversationId, isLast }: RunEntryProps) {
  const { t, language } = useI18n();
  const actions = useChatActions();
  const active = !isTerminalStatus(run.status);
  const [expandedOverride, setExpanded] = useState<boolean | null>(null);
  const expanded = expandedOverride ?? active;
  const cancelled = run.status === "cancelled";
  // Cancelled runs need their events for the partial answer (the stored reply is only a marker).
  const { data: view, isFetching } = useRunView(run, expanded || cancelled);

  const pendingQuestion = active ? (view ? view.pendingQuestion : run.pending_question) : null;
  const answer = cancelled ? (view?.answer ?? "") : (message?.content ?? view?.answer ?? "");
  const streaming = active && !message && Boolean(view?.answer);
  const usage = view?.usage ?? run.usage;
  const duration =
    view?.durationMs ??
    (run.started_at && run.finished_at ? Date.parse(run.finished_at) - Date.parse(run.started_at) : null);
  const showActivity = run.mode !== "chat" || active;
  const failedWithoutReply = run.status === "failed" && !message;

  return (
    <div className="flex flex-col gap-3">
      {showActivity && (
        <RunActivity
          run={run}
          view={view}
          loading={isFetching}
          expanded={expanded}
          onToggle={() => setExpanded(!expanded)}
        />
      )}

      {pendingQuestion && (
        <AskHumanCard
          question={pendingQuestion}
          onAnswer={(text) => actions.answer(conversationId, run.id, pendingQuestion.question_id, text)}
        />
      )}

      {answer && (
        <div className="group flex gap-3">
          <LogoMark className="mt-0.5 size-7 max-sm:hidden" />
          <div className="min-w-0 flex-1">
            <div
              data-testid="message-assistant"
              data-streaming={streaming || undefined}
              aria-busy={streaming || undefined}
            >
              <Markdown content={answer} conversationId={conversationId} className={cn(streaming && "md-streaming")} />
            </div>
            {!active && (
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-subtle">
                <CopyButton text={answer} label={t("chat.copyMessage")} className="-ml-1.5" />
                {usage && (usage.input_tokens > 0 || usage.completion_tokens > 0) && (
                  <span className="tabular-nums">
                    {t("activity.tokens", {
                      input: formatNumber(usage.input_tokens, language),
                      output: formatNumber(usage.completion_tokens, language),
                    })}
                  </span>
                )}
                {duration !== null && duration > 0 && (
                  <span className="tabular-nums">{formatDuration(duration, t)}</span>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {active && !answer && view?.groups.length && view.groups.every((group) => group.status !== "running") ? (
        <p className="pl-10 text-sm text-fg-subtle max-sm:pl-0">{t("activity.preparingAnswer")}</p>
      ) : null}

      {cancelled && (
        <p className="flex items-center gap-2 text-sm text-fg-subtle sm:pl-10">
          <Ban className="size-4 shrink-0" aria-hidden />
          {t("chat.stoppedReply")}
        </p>
      )}

      {failedWithoutReply && (
        <div role="alert" className="flex items-start gap-2.5 rounded-xl bg-danger-soft px-4 py-3 text-sm text-danger">
          <CircleX className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div className="min-w-0">
            <p className="font-medium">{t("activity.taskFailed")}</p>
            {(view?.error ?? run.error) && (
              <p className="mt-0.5 break-words text-danger/90">{view?.error ?? run.error}</p>
            )}
          </div>
        </div>
      )}

      {isLast && (run.status === "failed" || run.status === "cancelled") && (
        <div>
          <Button
            size="sm"
            variant="outline"
            icon={<RotateCcw className="size-4" />}
            onClick={() => actions.retry(conversationId)}
          >
            {t("chat.retry")}
          </Button>
        </div>
      )}
    </div>
  );
}
