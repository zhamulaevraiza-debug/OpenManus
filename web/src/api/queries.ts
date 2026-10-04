/** React Query keys and hooks for the REST API. */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "./client";

export const queryKeys = {
  me: ["me"] as const,
  authConfig: ["auth-config"] as const,
  status: ["status"] as const,
  health: ["health"] as const,
  agents: ["agents"] as const,
  conversations: (q = "") => ["conversations", q] as const,
  conversationsAll: ["conversations"] as const,
  conversation: (id: string) => ["conversation", id] as const,
  files: (conversationId: string) => ["files", conversationId] as const,
  runView: (runId: string) => ["run-view", runId] as const,
  settings: ["settings"] as const,
  users: ["users"] as const,
};

/** Retry transient failures only: never auth/permission/not-found/validation errors. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

/** The signed-in user, or null when there is no valid session. */
export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async () => {
      try {
        return await api.me();
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 5 * 60_000,
  });
}

export function useAuthConfig() {
  return useQuery({ queryKey: queryKeys.authConfig, queryFn: api.authConfig, staleTime: 60_000 });
}

export function useStatus(enabled = true) {
  return useQuery({ queryKey: queryKeys.status, queryFn: api.status, staleTime: 30_000, enabled });
}

export function useAgents() {
  return useQuery({ queryKey: queryKeys.agents, queryFn: api.agents, staleTime: 60_000 });
}

export function useConversations(q: string) {
  return useQuery({
    queryKey: queryKeys.conversations(q),
    queryFn: () => api.conversations(q || undefined),
    staleTime: 15_000,
    placeholderData: (previous) => previous,
  });
}

export function useConversation(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.conversation(id ?? ""),
    queryFn: () => api.conversation(id as string),
    enabled: Boolean(id),
    staleTime: 10_000,
  });
}

export function useFiles(conversationId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.files(conversationId ?? ""),
    queryFn: () => api.files(conversationId as string),
    enabled: Boolean(conversationId) && enabled,
    staleTime: 5_000,
  });
}

export function useSettings(enabled = true) {
  return useQuery({ queryKey: queryKeys.settings, queryFn: api.settings, enabled, staleTime: 30_000 });
}

export function useUsers(enabled = true) {
  return useQuery({ queryKey: queryKeys.users, queryFn: api.users, enabled });
}

export function useHealth() {
  return useQuery({ queryKey: queryKeys.health, queryFn: api.health, retry: false, staleTime: 15_000 });
}
