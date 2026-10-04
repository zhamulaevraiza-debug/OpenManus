import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { SettingsUpdate } from "@/api/types";
import { useToast } from "@/components/toast";
import { useI18n } from "@/i18n";
import { errorMessage } from "@/utils/errors";

/** PUT /settings (partial) and refresh dependent queries. */
export function useSaveSettings() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { t } = useI18n();
  return useMutation({
    mutationFn: (update: SettingsUpdate) => api.updateSettings(update),
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      void queryClient.invalidateQueries({ queryKey: queryKeys.status });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
      void queryClient.invalidateQueries({ queryKey: queryKeys.authConfig });
      toast.success(t("settings.saved"));
    },
    onError: (error) => toast.error(errorMessage(t, error, "settings.saveFailed")),
  });
}

/** Returns only the keys of `next` that differ from `base` (deep-compared via JSON). */
export function changedFields<T extends object>(base: T, next: T): Partial<T> {
  const changes: Partial<T> = {};
  for (const key of Object.keys(next) as Array<keyof T>) {
    if (JSON.stringify(base[key]) !== JSON.stringify(next[key])) changes[key] = next[key];
  }
  return changes;
}
