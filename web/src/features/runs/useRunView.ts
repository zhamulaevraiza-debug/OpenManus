/** Run view state in the React Query cache: live (SSE) for active runs, loaded on demand for finished ones. */
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { queryKeys } from "@/api/queries";
import { subscribeRunEvents, type StreamState } from "@/api/sse";
import type { ConversationDetail, Run, RunEvent, RunStatus } from "@/api/types";
import { useI18n } from "@/i18n";
import { notifyInBackground } from "@/utils/notify";

import { initialRunView, isTerminalStatus, reduceRunEvents, type RunView } from "./reducer";

const PAGE_SIZE = 500;

/** Fetches the run's persisted events after `base` (or from the start), paginated, and reduces them. */
export async function loadRunView(run: Run, base?: RunView): Promise<RunView> {
  let view = base ?? initialRunView(run);
  for (;;) {
    const events = await api.runEvents(run.id, view.lastSeq);
    view = reduceRunEvents(view, events);
    if (events.length < PAGE_SIZE) return view;
  }
}

/**
 * Reads a run's view from the cache (active runs are fed by `useLiveRun`). Finished runs load their
 * events when `enabled`; a view cached while the run was still active is completed from its last seq.
 */
export function useRunView(run: Run, enabled: boolean) {
  const queryClient = useQueryClient();
  const key = queryKeys.runView(run.id);
  return useQuery({
    queryKey: key,
    queryFn: () => loadRunView(run, queryClient.getQueryData<RunView>(key)),
    enabled: enabled && isTerminalStatus(run.status),
    staleTime: (query) => (query.state.data && !isTerminalStatus(query.state.data.status) ? 0 : Infinity),
    gcTime: 30 * 60_000,
  });
}

function patchRun(queryClient: QueryClient, conversationId: string, runId: string, patch: Partial<Run>) {
  queryClient.setQueryData<ConversationDetail>(queryKeys.conversation(conversationId), (detail) =>
    detail ? { ...detail, runs: detail.runs.map((run) => (run.id === runId ? { ...run, ...patch } : run)) } : detail,
  );
}

function scheduleFlush(callback: () => void): () => void {
  if (document.visibilityState === "hidden" || typeof requestAnimationFrame !== "function") {
    const timer = setTimeout(callback, 200);
    return () => clearTimeout(timer);
  }
  const frame = requestAnimationFrame(callback);
  return () => cancelAnimationFrame(frame);
}

/**
 * Streams an active run into the cache and keeps related queries fresh
 * (conversation, files, status) plus background notifications.
 */
export function useLiveRun(conversationId: string, run: Run | null): StreamState | null {
  const queryClient = useQueryClient();
  const { t } = useI18n();
  const [streamState, setStreamState] = useState<StreamState | null>(null);
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const runId = run && !isTerminalStatus(run.status) ? run.id : null;
  const runRef = useRef(run);
  useEffect(() => {
    runRef.current = run;
  }, [run]);

  useEffect(() => {
    const current = runRef.current;
    if (!runId || !current) return;
    const key = queryKeys.runView(runId);
    if (!queryClient.getQueryData<RunView>(key)) queryClient.setQueryData(key, initialRunView(current));
    const after = queryClient.getQueryData<RunView>(key)?.lastSeq ?? 0;

    let pending: RunEvent[] = [];
    let cancelFlush: (() => void) | null = null;
    const flush = () => {
      cancelFlush = null;
      const batch = pending;
      pending = [];
      queryClient.setQueryData<RunView>(key, (view) => reduceRunEvents(view ?? initialRunView(current), batch));
    };

    const onEvents = (events: RunEvent[]) => {
      pending.push(...events);
      cancelFlush ??= scheduleFlush(flush);
      for (const event of events) handleSideEffects(event);
    };

    const handleSideEffects = (event: RunEvent) => {
      const data = event.data;
      switch (event.type) {
        case "workspace.changed":
          void queryClient.invalidateQueries({ queryKey: queryKeys.files(conversationId) });
          break;
        case "run.started":
          patchRun(queryClient, conversationId, runId, { status: "running", started_at: event.ts });
          break;
        case "run.status":
          if (typeof data.status === "string")
            patchRun(queryClient, conversationId, runId, { status: data.status as RunStatus });
          break;
        case "human.question": {
          const question = typeof data.question === "string" ? data.question : "";
          const questionId = typeof data.question_id === "string" ? data.question_id : "";
          patchRun(queryClient, conversationId, runId, {
            status: "waiting_input",
            pending_question: { question_id: questionId, question },
          });
          void notifyInBackground(tRef.current("notifications.needsAnswer"), {
            body: question,
            tag: `question-${runId}`,
          });
          break;
        }
        case "human.answer":
          patchRun(queryClient, conversationId, runId, { pending_question: null });
          break;
        case "run.finished": {
          const status = (typeof data.status === "string" ? data.status : "completed") as RunStatus;
          // Reflect the end immediately (composer, sidebar); the refetch below brings the reply.
          patchRun(queryClient, conversationId, runId, {
            status,
            finished_at: event.ts,
            pending_question: null,
            error: typeof data.error === "string" ? data.error : null,
          });
          const title =
            status === "completed"
              ? tRef.current("notifications.finished")
              : status === "cancelled"
                ? tRef.current("notifications.stopped")
                : tRef.current("notifications.failed");
          const conversation = queryClient.getQueryData<ConversationDetail>(queryKeys.conversation(conversationId));
          void notifyInBackground(title, { body: conversation?.conversation.title, tag: `run-${runId}` });
          if (cancelFlush) {
            cancelFlush();
            flush();
          }
          void queryClient.invalidateQueries({ queryKey: queryKeys.conversation(conversationId) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.conversationsAll });
          void queryClient.invalidateQueries({ queryKey: queryKeys.files(conversationId) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.status });
          break;
        }
        default:
          break;
      }
    };

    const subscription = subscribeRunEvents({
      runId,
      after,
      onEvents,
      onStateChange: setStreamState,
      onFatal: () => {
        void queryClient.invalidateQueries({ queryKey: queryKeys.conversation(conversationId) });
      },
    });
    return () => {
      subscription.close();
      if (cancelFlush) {
        cancelFlush();
        flush();
      }
    };
  }, [conversationId, runId, queryClient]);

  return runId ? streamState : null;
}
