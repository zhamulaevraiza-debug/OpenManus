import { ArrowDown } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import type { ConversationDetail } from "@/api/types";
import { Markdown } from "@/components/Markdown";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { RunEntry, UserMessage } from "./Messages";
import { buildTimeline } from "./timeline";

const STICKY_THRESHOLD = 96;

/** Scrollable conversation that sticks to the bottom while new content streams in. */
export function MessageList({ detail }: { detail: ConversationDetail }) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const entries = useMemo(() => buildTimeline(detail.messages, detail.runs), [detail.messages, detail.runs]);
  const lastRunIndex = entries.findLastIndex((entry) => entry.kind === "run");
  const conversationId = detail.conversation.id;

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const element = scrollRef.current;
    if (element) element.scrollTo({ top: element.scrollHeight, behavior });
  }, []);

  // New conversation: start at the bottom.
  useLayoutEffect(() => {
    stickToBottom.current = true;
    scrollToBottom();
  }, [conversationId, scrollToBottom]);

  // Follow content growth (streaming answers, new events, expanding cards) while pinned to the bottom.
  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (stickToBottom.current) scrollToBottom();
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [scrollToBottom]);

  // Sending a message always scrolls to it.
  const userCount = detail.messages.filter((message) => message.role === "user").length;
  useEffect(() => {
    stickToBottom.current = true;
    scrollToBottom("smooth");
  }, [userCount, scrollToBottom]);

  const onScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    stickToBottom.current = distance < STICKY_THRESHOLD;
    setShowJump(distance > STICKY_THRESHOLD * 3);
  };

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="scroll-area h-full overflow-y-auto"
        role="log"
        aria-label={t("chat.conversation")}
        aria-live="off"
      >
        <div ref={contentRef} className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 pt-6 pb-8 md:px-6">
          {entries.map((entry, index) =>
            entry.kind === "message" ? (
              entry.message.role === "user" ? (
                <UserMessage key={entry.message.id} message={entry.message} />
              ) : (
                <AssistantMessage
                  key={entry.message.id}
                  conversationId={conversationId}
                  content={entry.message.content}
                />
              )
            ) : (
              <RunEntry
                key={entry.run.id}
                run={entry.run}
                message={entry.message}
                conversationId={conversationId}
                isLast={index === lastRunIndex}
              />
            ),
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => {
          stickToBottom.current = true;
          scrollToBottom("smooth");
        }}
        aria-label={t("chat.jumpToLatest")}
        title={t("chat.jumpToLatest")}
        tabIndex={showJump ? 0 : -1}
        className={cn(
          "absolute bottom-3 left-1/2 inline-flex size-10 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-surface text-fg-muted shadow-md transition-all duration-200 hover:text-fg",
          showJump ? "opacity-100" : "pointer-events-none translate-y-2 opacity-0",
        )}
      >
        <ArrowDown className="size-[18px]" />
      </button>
    </div>
  );
}

/** An assistant message that isn't linked to a run. */
function AssistantMessage({ conversationId, content }: { conversationId: string; content: string }) {
  return (
    <div data-testid="message-assistant">
      <Markdown conversationId={conversationId} content={content} />
    </div>
  );
}
