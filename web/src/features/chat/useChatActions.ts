import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { Conversation, ConversationDetail, Message, Run } from "@/api/types";
import { useToast } from "@/components/toast";
import { useI18n } from "@/i18n";
import { errorMessage } from "@/utils/errors";

import { moveDraft, NEW_DRAFT, releaseDraft, updateDraft, type Draft } from "./drafts";

let localId = 0;

/** Chat mutations with optimistic cache updates. */
export function useChatActions() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const { t } = useI18n();

  const setDetail = useCallback(
    (conversationId: string, update: (detail: ConversationDetail) => ConversationDetail) => {
      queryClient.setQueryData<ConversationDetail>(queryKeys.conversation(conversationId), (detail) =>
        detail ? update(detail) : detail,
      );
    },
    [queryClient],
  );

  const addRun = useCallback(
    (conversationId: string, run: Run) => {
      setDetail(conversationId, (detail) => ({
        ...detail,
        conversation: { ...detail.conversation, active_run_id: run.id },
        runs: [...detail.runs.filter((existing) => existing.id !== run.id), run],
      }));
      // The server may have auto-titled the conversation; refresh lists and the header.
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversationsAll });
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversation(conversationId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.status });
    },
    [queryClient, setDetail],
  );

  /** Creates a conversation for the new-chat page and moves its draft there (without navigating). */
  const createConversation = useCallback(
    async (mode: string): Promise<Conversation> => {
      const conversation = await api.createConversation({ mode });
      queryClient.setQueryData<ConversationDetail>(queryKeys.conversation(conversation.id), {
        conversation,
        messages: [],
        runs: [],
      });
      moveDraft(NEW_DRAFT, conversation.id);
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversationsAll });
      return conversation;
    },
    [queryClient],
  );

  /**
   * Sends a message. The caller has already cleared the composer and passes the cleared draft as
   * `snapshot`; on failure it is restored and an error is shown.
   */
  const send = useCallback(
    async (input: {
      conversationId: string | null;
      content: string;
      mode: string;
      attachments: string[];
      snapshot: Draft;
    }) => {
      let conversationId = input.conversationId;
      const restore = (key: string) =>
        updateDraft(key, (draft) => (draft.text || draft.attachments.length ? draft : input.snapshot));
      try {
        if (!conversationId) {
          conversationId = (await createConversation(input.mode)).id;
          navigate(`/c/${conversationId}`);
        }
      } catch (error) {
        restore(NEW_DRAFT);
        toast.error(errorMessage(t, error, "chat.sendFailed"));
        return;
      }

      const optimistic: Message = {
        id: `local-${++localId}`,
        conversation_id: conversationId,
        role: "user",
        content: input.content,
        run_id: null,
        attachments: input.attachments,
        created_at: new Date().toISOString(),
      };
      setDetail(conversationId, (detail) => ({ ...detail, messages: [...detail.messages, optimistic] }));

      try {
        const result = await api.sendMessage(conversationId, {
          content: input.content,
          mode: input.mode,
          attachments: input.attachments.length ? input.attachments : undefined,
        });
        setDetail(conversationId, (detail) => ({
          ...detail,
          conversation: { ...detail.conversation, mode: input.mode },
          messages: detail.messages.map((message) => (message.id === optimistic.id ? result.message : message)),
        }));
        addRun(conversationId, result.run);
        releaseDraft(input.snapshot);
      } catch (error) {
        const id = conversationId;
        setDetail(id, (detail) => ({
          ...detail,
          messages: detail.messages.filter((message) => message.id !== optimistic.id),
        }));
        restore(id);
        let message = errorMessage(t, error, "chat.sendFailed");
        if (error instanceof ApiError && error.status === 409) message = t("chat.runBusy");
        if (error instanceof ApiError && error.status === 429) message = t("chat.limitReached");
        toast.error(message);
        if (error instanceof ApiError && error.status === 409) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.conversation(id) });
        }
      }
    },
    [addRun, createConversation, navigate, queryClient, setDetail, t, toast],
  );

  const stop = useCallback(
    async (conversationId: string, runId: string) => {
      try {
        const run = await api.cancelRun(runId);
        setDetail(conversationId, (detail) => ({
          ...detail,
          runs: detail.runs.map((existing) => (existing.id === run.id ? { ...existing, ...run } : existing)),
        }));
      } catch (error) {
        toast.error(errorMessage(t, error, "chat.stopFailed"));
      }
    },
    [setDetail, t, toast],
  );

  const retry = useCallback(
    async (conversationId: string) => {
      try {
        const result = await api.retry(conversationId);
        addRun(conversationId, result.run);
      } catch (error) {
        toast.error(
          error instanceof ApiError && error.status === 409
            ? t("chat.runBusy")
            : errorMessage(t, error, "chat.retryFailed"),
        );
      }
    },
    [addRun, t, toast],
  );

  const answer = useCallback(
    async (conversationId: string, runId: string, questionId: string, text: string) => {
      try {
        await api.answer(runId, questionId, text);
        setDetail(conversationId, (detail) => ({
          ...detail,
          runs: detail.runs.map((run) =>
            run.id === runId ? { ...run, pending_question: null, status: "running" } : run,
          ),
        }));
        return true;
      } catch (error) {
        toast.error(errorMessage(t, error, "askHuman.failed"));
        if (error instanceof ApiError && error.status === 404) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.conversation(conversationId) });
        }
        return false;
      }
    },
    [queryClient, setDetail, t, toast],
  );

  return { createConversation, send, stop, retry, answer };
}
