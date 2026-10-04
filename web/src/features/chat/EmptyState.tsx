import { ArrowUpRight } from "lucide-react";

import { LogoMark } from "@/components/Logo";
import { AgentBadge } from "@/features/agents/AgentBadge";
import { agentDescription, suggestionKeys } from "@/features/agents/meta";
import { useI18n } from "@/i18n";

import { updateDraft } from "./drafts";

interface EmptyStateProps {
  draftKey: string;
  mode: string;
  username?: string;
  agentDescriptionFallback?: string | null;
}

/** Greeting with localized example prompts for the selected mode. */
export function EmptyState({ draftKey, mode, username, agentDescriptionFallback }: EmptyStateProps) {
  const { t } = useI18n();
  const prompts = suggestionKeys(mode).map((key) => t(key));

  const applyPrompt = (prompt: string) => {
    updateDraft(draftKey, (draft) => ({ ...draft, text: prompt }));
    const input = document.querySelector<HTMLTextAreaElement>('[data-testid="composer-input"]');
    input?.focus();
    input?.setSelectionRange(prompt.length, prompt.length);
  };

  return (
    <div className="scroll-area min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col items-center justify-center px-4 py-6 sm:py-10 md:px-6">
        <div className="relative mb-4 sm:mb-5">
          <div className="absolute inset-0 -z-10 scale-150 rounded-full bg-accent/20 blur-2xl" aria-hidden />
          <LogoMark className="size-12 shadow-lg shadow-indigo-500/20 sm:size-14" />
        </div>
        <h1 className="text-center text-2xl font-semibold tracking-tight text-fg md:text-3xl">
          {username ? t("chat.greetingNamed", { name: username }) : t("chat.greeting")}
        </h1>
        <div className="mt-3 flex max-w-lg flex-col items-center gap-2 text-center">
          <AgentBadge agentKey={mode} size="md" />
          <p className="text-sm leading-relaxed text-fg-muted">{agentDescription(t, mode, agentDescriptionFallback)}</p>
        </div>
        <ul
          aria-label={t("chat.suggestionsLabel")}
          className="mt-6 grid w-full gap-2 sm:mt-8 sm:grid-cols-2 sm:gap-2.5"
        >
          {prompts.map((prompt, index) => (
            <li key={prompt} className={index === 3 ? "max-sm:hidden" : undefined}>
              <button
                type="button"
                onClick={() => applyPrompt(prompt)}
                className="group flex h-full min-h-12 w-full items-start gap-3 rounded-2xl border border-border bg-surface px-3.5 py-2.5 text-left text-sm leading-snug text-fg-muted shadow-xs transition-all duration-150 hover:-translate-y-px hover:border-border-strong hover:text-fg hover:shadow-sm sm:min-h-14 sm:px-4 sm:py-3"
              >
                <span className="min-w-0 flex-1">{prompt}</span>
                <ArrowUpRight
                  className="mt-0.5 size-4 shrink-0 text-fg-subtle opacity-60 transition-opacity group-hover:opacity-100"
                  aria-hidden
                />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
