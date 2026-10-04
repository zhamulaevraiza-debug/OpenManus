import { X } from "lucide-react";
import { useId, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { Button, IconButton } from "./Button";
import { Field, Input } from "./Form";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  initialFocus?: RefObject<HTMLElement | null>;
  className?: string;
}

const SIZES = { sm: "sm:max-w-sm", md: "sm:max-w-lg", lg: "sm:max-w-2xl" };

/** Accessible modal dialog; a bottom sheet on phones. */
export function Modal(props: ModalProps) {
  if (!props.open) return null;
  return createPortal(<ModalContent {...props} />, document.body);
}

function ModalContent({
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  initialFocus,
  className,
}: ModalProps) {
  const { t } = useI18n();
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useBodyScrollLock(true);
  useFocusTrap(panelRef, true, { onEscape: onClose, initialFocus });

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-6">
      <div className="absolute inset-0 animate-fade-in bg-overlay backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative flex max-h-[min(92dvh,var(--app-height))] w-full animate-slide-up flex-col overflow-hidden rounded-t-3xl border border-border bg-surface shadow-lg outline-none sm:animate-pop-in sm:rounded-2xl",
          SIZES[size],
          className,
        )}
      >
        <div className="flex items-start gap-3 px-5 pt-5 pb-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-lg font-semibold tracking-tight text-fg">
              {title}
            </h2>
            {description && (
              <div id={descriptionId} className="mt-1 text-sm leading-relaxed text-fg-muted">
                {description}
              </div>
            )}
          </div>
          <IconButton label={t("common.close")} size="sm" onClick={onClose} className="-mt-1 -mr-2">
            <X />
          </IconButton>
        </div>
        {children && <div className="scroll-area min-h-0 flex-1 overflow-y-auto px-5 pb-2 sm:px-6">{children}</div>}
        {footer && (
          <div className="pb-safe flex flex-col-reverse gap-2 px-5 pt-3 pb-5 sm:flex-row sm:justify-end sm:px-6 [&>*]:max-sm:w-full">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  body?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => Promise<unknown> | void;
  onClose: () => void;
}

export function ConfirmDialog({ open, title, body, confirmLabel, danger, onConfirm, onClose }: ConfirmDialogProps) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
      onClose();
    } catch {
      // `onConfirm` reports its own errors; keep the dialog open for another attempt.
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={body}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={confirm}>
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}

interface PromptDialogProps {
  open: boolean;
  title: ReactNode;
  label: string;
  initialValue?: string;
  submitLabel: string;
  type?: "text" | "password";
  minLength?: number;
  maxLength?: number;
  autoComplete?: string;
  onSubmit: (value: string) => Promise<unknown> | void;
  onClose: () => void;
}

export function PromptDialog(props: PromptDialogProps) {
  if (!props.open) return null;
  return <PromptDialogContent {...props} />;
}

function PromptDialogContent({
  title,
  label,
  initialValue = "",
  submitLabel,
  type = "text",
  minLength,
  maxLength,
  autoComplete = "off",
  onSubmit,
  onClose,
}: PromptDialogProps) {
  const { t } = useI18n();
  const [value, setValue] = useState(initialValue);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = type === "password" ? value : value.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await onSubmit(trimmed);
      onClose();
    } catch {
      // `onSubmit` reports its own errors; keep the dialog open for another attempt.
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      size="sm"
      initialFocus={inputRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" type="submit" form={formId} loading={busy} disabled={!value.trim()}>
            {submitLabel}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="pb-2">
        <Field label={label}>
          {(fieldProps) => (
            <Input
              {...fieldProps}
              ref={inputRef}
              type={type}
              value={value}
              minLength={minLength}
              maxLength={maxLength}
              autoComplete={autoComplete}
              onChange={(event) => setValue(event.target.value)}
              onFocus={(event) => event.currentTarget.select()}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
