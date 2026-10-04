import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useMatch, useNavigate } from "react-router-dom";

import { api } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { Conversation, ConversationDetail } from "@/api/types";
import { useToast } from "@/components/toast";
import { useI18n } from "@/i18n";
import { errorMessage } from "@/utils/errors";

/** Rename / pin / delete with cache updates and error toasts. Errors are re-thrown for dialogs. */
export function useConversationActions() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { t } = useI18n();
  const navigate = useNavigate();
  const match = useMatch("/c/:conversationId");
  const currentId = match?.params.conversationId;

  const applyUpdate = useCallback(
    (updated: Conversation) => {
      queryClient.setQueryData<ConversationDetail>(queryKeys.conversation(updated.id), (detail) =>
        detail ? { ...detail, conversation: updated } : detail,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversationsAll });
    },
    [queryClient],
  );

  const update = useCallback(
    async (conversation: Conversation, patch: { title?: string; pinned?: boolean; mode?: string }) => {
      try {
        applyUpdate(await api.updateConversation(conversation.id, patch));
      } catch (error) {
        toast.error(errorMessage(t, error));
        throw error;
      }
    },
    [applyUpdate, toast, t],
  );

  const remove = useCallback(
    async (conversation: Conversation) => {
      try {
        await api.deleteConversation(conversation.id);
      } catch (error) {
        toast.error(errorMessage(t, error));
        throw error;
      }
      if (currentId === conversation.id) navigate("/", { replace: true });
      queryClient.removeQueries({ queryKey: queryKeys.conversation(conversation.id) });
      queryClient.removeQueries({ queryKey: queryKeys.files(conversation.id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.conversationsAll });
      toast.success(t("nav.deleted"));
    },
    [currentId, navigate, queryClient, toast, t],
  );

  return {
    rename: (conversation: Conversation, title: string) => update(conversation, { title }),
    togglePin: (conversation: Conversation) => update(conversation, { pinned: !conversation.pinned }),
    setMode: (conversation: Conversation, mode: string) => update(conversation, { mode }),
    remove,
  };
}
