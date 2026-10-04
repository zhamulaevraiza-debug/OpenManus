import { ApiError } from "@/api/client";
import type { Translate, TranslationKey } from "@/i18n";

/**
 * User-facing text for a failed request. Known statuses get a localized message; otherwise the
 * server's `detail` is shown, falling back to `fallbackKey`.
 */
export function errorMessage(t: Translate, error: unknown, fallbackKey: TranslationKey = "errors.generic"): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return t("errors.network");
    if (error.status === 403) return t("errors.forbidden");
    if (error.status === 404) return t("errors.notFound");
    if (error.status === 413) return t("errors.tooLarge");
    if (error.status === 429) return t("errors.rateLimited");
    if (error.status >= 500) return t(fallbackKey);
    if (error.message) return error.message;
  }
  return t(fallbackKey);
}
