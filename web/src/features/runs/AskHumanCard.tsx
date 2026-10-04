import { MessageCircleQuestionMark, SendHorizontal } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import type { PendingQuestion } from "@/api/types";
import { Button } from "@/components/Button";
import { Markdown } from "@/components/Markdown";
import { useIsTouch } from "@/hooks/useMediaQuery";
import { useI18n } from "@/i18n";

interface AskHumanCardProps {
  question: PendingQuestion;
  onAnswer: (answer: string) => Promise<boolean>;
}

/** Prominent card asking the user to answer an agent's question. */
export function AskHumanCard({ question, onAnswer }: AskHumanCardProps) {
  const { t } = useI18n();
  const isTouch = useIsTouch();
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!isTouch) inputRef.current?.focus({ preventScroll: true });
  }, [question.question_id, isTouch]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const text = answer.trim();
    if (!text || busy) return;
    setBusy(true);
    const ok = await onAnswer(text);
    setBusy(false);
    if (ok) setAnswer("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !isTouch) {
      event.preventDefault();
      void submit();
    }
  };

  return (
    <form
      onSubmit={submit}
      aria-label={t("askHuman.title")}
      className="animate-pop-in rounded-2xl border border-warning/40 bg-warning-soft p-4 shadow-sm"
    >
      <div className="flex items-center gap-2.5">
        <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-warning/15 text-warning">
          <MessageCircleQuestionMark className="size-[18px]" aria-hidden />
        </span>
        <h3 className="text-sm font-semibold text-fg">{t("askHuman.title")}</h3>
      </div>
      <Markdown content={question.question} className="mt-3 text-fg" />
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
        <textarea
          ref={inputRef}
          data-testid="ask-human-input"
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          placeholder={t("askHuman.placeholder")}
          aria-label={t("askHuman.placeholder")}
          className="min-h-12 w-full flex-1 resize-y rounded-xl border border-border bg-surface px-3.5 py-2.5 text-base leading-relaxed text-fg placeholder:text-fg-subtle focus:border-accent focus:ring-4 focus:ring-accent/15 focus:outline-none md:text-[0.9375rem]"
        />
        <Button
          type="submit"
          variant="primary"
          data-testid="ask-human-send"
          loading={busy}
          disabled={!answer.trim()}
          icon={<SendHorizontal className="size-4" />}
          className="sm:h-12"
        >
          {t("askHuman.send")}
        </Button>
      </div>
    </form>
  );
}
