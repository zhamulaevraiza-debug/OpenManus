import { useQueryClient } from "@tanstack/react-query";
import { Ellipsis, FolderOpen, MessageSquareDashed } from "lucide-react";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import { queryKeys, useAgents, useConversation, useMe } from "@/api/queries";
import type { ConversationDetail } from "@/api/types";
import { Button, IconButton } from "@/components/Button";
import { Skeleton } from "@/components/Skeleton";
import { findAgent } from "@/features/agents/meta";
import { Composer } from "@/features/chat/Composer";
import { NEW_DRAFT } from "@/features/chat/drafts";
import { EmptyState } from "@/features/chat/EmptyState";
import { MessageList } from "@/features/chat/MessageList";
import { useChatActions } from "@/features/chat/useChatActions";
import { ConversationMenu } from "@/features/conversations/ConversationMenu";
import { useConversationActions } from "@/features/conversations/useConversationActions";
import { FilesPanel } from "@/features/files/FilesPanel";
import { FilesPanelContext, type FilesPanelControl } from "@/features/files/panel";
import { isTerminalStatus } from "@/features/runs/reducer";
import { useLiveRun } from "@/features/runs/useRunView";
import { TopBar } from "@/features/shell/TopBar";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useI18n } from "@/i18n";

const MODE_KEY = "om.mode";
const FILES_KEY = "om.filesOpen";

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage unavailable
  }
}

/** Route wrapper: a fresh page per conversation (drafts live outside and survive). */
export function ChatRoute() {
  const { conversationId } = useParams();
  return <ChatPage key={conversationId ?? NEW_DRAFT} conversationId={conversationId ?? null} />;
}

function ChatPage({ conversationId }: { conversationId: string | null }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const { data: detail, isPending, error, refetch } = useConversation(conversationId ?? undefined);
  const { data: agentsData } = useAgents();
  const { data: me } = useMe();
  const conversationActions = useConversationActions();
  const chat = useChatActions();
  const [newMode, setNewMode] = useState(() => readStored(MODE_KEY) ?? "auto");
  const [filesOpen, setFilesOpen] = useState(() => readStored(FILES_KEY) === "1" && window.innerWidth >= 1024);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);

  const conversation = detail?.conversation ?? null;
  const mode = conversation?.mode ?? newMode;
  const activeRun = useMemo(() => detail?.runs.find((run) => !isTerminalStatus(run.status)) ?? null, [detail]);
  useLiveRun(conversationId ?? "", conversationId ? activeRun : null);

  const setFiles = useCallback((open: boolean) => {
    setFilesOpen(open);
    if (window.innerWidth >= 1024) writeStored(FILES_KEY, open ? "1" : "0");
  }, []);

  const filesControl = useMemo<FilesPanelControl>(
    () => ({
      open: filesOpen,
      show: (path) => {
        setSelectedPath(path ?? null);
        setFiles(true);
      },
      hide: () => setFiles(false),
      toggle: () => setFiles(!filesOpen),
    }),
    [filesOpen, setFiles],
  );

  const changeMode = (next: string) => {
    writeStored(MODE_KEY, next);
    if (!conversation) {
      setNewMode(next);
      return;
    }
    queryClient.setQueryData<ConversationDetail>(queryKeys.conversation(conversation.id), (current) =>
      current ? { ...current, conversation: { ...current.conversation, mode: next } } : current,
    );
    conversationActions.setMode(conversation, next).catch(() => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversation(conversation.id) });
    });
  };

  const notFound = error instanceof ApiError && error.status === 404;
  const hasHistory = Boolean(detail && (detail.messages.length > 0 || detail.runs.length > 0));
  const title = conversation ? conversation.title || t("nav.untitled") : t("nav.newChat");
  useDocumentTitle(conversation?.title || null);

  let body;
  if (conversationId && notFound) {
    body = <NotFoundState />;
  } else if (conversationId && error && !detail) {
    body = (
      <CenteredMessage title={t("chat.loadFailed")}>
        <Button variant="outline" onClick={() => refetch()}>
          {t("common.retry")}
        </Button>
      </CenteredMessage>
    );
  } else if (conversationId && isPending) {
    body = <ChatSkeleton />;
  } else if (detail && hasHistory) {
    body = <MessageList detail={detail} />;
  } else {
    body = (
      <EmptyState
        draftKey={conversationId ?? NEW_DRAFT}
        mode={mode}
        username={me?.username}
        agentDescriptionFallback={findAgent(agentsData?.agents, mode)?.description}
      />
    );
  }

  return (
    <FilesPanelContext.Provider value={filesControl}>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar
            title={<h1 className="truncate text-[0.9375rem] font-semibold text-fg">{title}</h1>}
            actions={
              <>
                <IconButton
                  label={t("chat.files")}
                  data-testid="files-button"
                  aria-pressed={filesOpen}
                  onClick={filesControl.toggle}
                  className={filesOpen ? "bg-surface-2 text-fg" : undefined}
                >
                  <FolderOpen />
                </IconButton>
                {conversation && (
                  <>
                    <IconButton
                      ref={menuButton}
                      label={t("nav.chatActions")}
                      aria-haspopup="menu"
                      aria-expanded={menuOpen}
                      onClick={() => setMenuOpen((open) => !open)}
                    >
                      <Ellipsis />
                    </IconButton>
                    <ConversationMenu
                      conversation={conversation}
                      anchorRef={menuButton}
                      open={menuOpen}
                      onClose={() => setMenuOpen(false)}
                    />
                  </>
                )}
              </>
            }
          />
          {body}
          {!notFound && (
            <Composer
              conversationId={conversationId}
              mode={mode}
              onModeChange={changeMode}
              modes={agentsData?.modes}
              agents={agentsData?.agents}
              activeRun={activeRun}
              onStop={() => activeRun && conversationId && chat.stop(conversationId, activeRun.id)}
            />
          )}
        </div>
        {filesOpen && (
          <FilesPanel
            conversationId={conversationId}
            selectedPath={selectedPath}
            onSelect={setSelectedPath}
            onClose={() => setFiles(false)}
          />
        )}
      </div>
    </FilesPanelContext.Provider>
  );
}

function ChatSkeleton() {
  return (
    <div className="min-h-0 flex-1 overflow-hidden" aria-busy="true">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 pt-8 md:px-6">
        <Skeleton className="ml-auto h-11 w-2/3 rounded-3xl" />
        <Skeleton className="h-12 w-full rounded-2xl" />
        <div className="flex flex-col gap-2.5">
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>
        <Skeleton className="ml-auto h-11 w-1/2 rounded-3xl" />
        <Skeleton className="h-12 w-full rounded-2xl" />
      </div>
    </div>
  );
}

function CenteredMessage({ title, body, children }: { title: string; body?: string; children?: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
      <span className="inline-flex size-14 items-center justify-center rounded-2xl bg-surface-2 text-fg-subtle">
        <MessageSquareDashed className="size-7" aria-hidden />
      </span>
      <h2 className="text-lg font-semibold text-fg">{title}</h2>
      {body && <p className="max-w-sm text-sm text-fg-muted">{body}</p>}
      {children}
    </div>
  );
}

function NotFoundState() {
  const { t } = useI18n();
  return (
    <CenteredMessage title={t("chat.notFoundTitle")} body={t("chat.notFoundBody")}>
      <Link
        to="/"
        className="inline-flex h-10 items-center rounded-xl bg-accent px-4 text-sm font-medium text-on-accent hover:bg-accent-hover"
      >
        {t("chat.startNew")}
      </Link>
    </CenteredMessage>
  );
}
