import { Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { AgentInfo, Mode } from "@/api/types";
import { Popover } from "@/components/Popover";
import { AgentIcon, AgentTile } from "@/features/agents/AgentBadge";
import { agentDescription, agentName } from "@/features/agents/meta";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

interface ModeOption {
  key: string;
  name: string;
  description: string;
  icon: string | null;
  available: boolean;
  reason: string | null;
}

interface ModePickerProps {
  value: string;
  onChange: (mode: string) => void;
  modes: readonly Mode[] | undefined;
  agents: readonly AgentInfo[] | undefined;
  disabled?: boolean;
}

const DEFAULT_MODES: readonly Mode[] = [{ key: "auto" }, { key: "chat" }, { key: "team" }];

/** Mode selector: Auto, Chat, Team, then every agent (unavailable ones disabled with the reason). */
export function ModePicker({ value, onChange, modes, agents, disabled }: ModePickerProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const builtin: ModeOption[] = (modes?.length ? modes : DEFAULT_MODES).map((mode) => ({
    key: mode.key,
    name: agentName(t, mode.key),
    description: agentDescription(t, mode.key),
    icon: null,
    available: true,
    reason: null,
  }));
  const agentOptions: ModeOption[] = (agents ?? []).map((agent) => ({
    key: agent.key,
    name: agentName(t, agent.key, agent.name),
    description: agentDescription(t, agent.key, agent.description),
    icon: agent.icon,
    available: agent.available,
    reason: agent.reason,
  }));
  const current = [...builtin, ...agentOptions].find((option) => option.key === value);

  const select = (key: string) => {
    setOpen(false);
    triggerRef.current?.focus();
    if (key !== value) onChange(key);
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        data-testid="mode-picker"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${t("modes.label")}: ${current?.name ?? value}`}
        disabled={disabled}
        onClick={() => setOpen((isOpen) => !isOpen)}
        className={cn(
          "inline-flex h-9 max-w-[11rem] min-w-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-sm font-medium text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:opacity-50 max-md:h-10",
          open && "bg-surface-2 text-fg",
        )}
      >
        <AgentIcon agentKey={value} iconName={current?.icon} className="size-4 shrink-0" />
        <span className="truncate max-[359px]:hidden">{current?.name ?? agentName(t, value)}</span>
        <ChevronDown className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      <Popover
        anchorRef={triggerRef}
        open={open}
        onClose={() => setOpen(false)}
        placement="top-start"
        sheetOnMobile
        className="md:w-[22rem]"
      >
        <OptionList value={value} builtin={builtin} agents={agentOptions} onSelect={select} />
      </Popover>
    </>
  );
}

function OptionList({
  value,
  builtin,
  agents,
  onSelect,
}: {
  value: string;
  builtin: ModeOption[];
  agents: ModeOption[];
  onSelect: (key: string) => void;
}) {
  const { t } = useI18n();
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const list = listRef.current;
    const selected =
      list?.querySelector<HTMLElement>('[aria-selected="true"]') ?? list?.querySelector<HTMLElement>("[role=option]");
    selected?.focus();
    selected?.scrollIntoView({ block: "nearest" });
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const options = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[role=option]:not([aria-disabled=true])") ?? [],
    );
    const index = options.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = Math.min(options.length - 1, index + 1);
    else if (event.key === "ArrowUp") next = Math.max(0, index - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = options.length - 1;
    if (next !== null && options[next]) {
      event.preventDefault();
      options[next].focus();
    }
  };

  const renderOption = (option: ModeOption) => {
    const selected = option.key === value;
    const unavailable = !option.available;
    return (
      <div
        key={option.key}
        role="option"
        tabIndex={unavailable ? -1 : 0}
        aria-selected={selected}
        aria-disabled={unavailable || undefined}
        data-testid={`mode-option-${option.key}`}
        onClick={() => !unavailable && onSelect(option.key)}
        onKeyDown={(event) => {
          if ((event.key === "Enter" || event.key === " ") && !unavailable) {
            event.preventDefault();
            onSelect(option.key);
          }
        }}
        className={cn(
          "flex items-start gap-3 rounded-xl px-2.5 py-2 transition-colors outline-none max-md:py-2.5",
          unavailable
            ? "cursor-not-allowed opacity-55"
            : "cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2",
          selected && "bg-accent-soft hover:bg-accent-soft",
        )}
      >
        <AgentTile agentKey={option.key} iconName={option.icon} className={cn(unavailable && "grayscale")} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-fg">{option.name}</span>
            {unavailable && (
              <span className="shrink-0 rounded-full bg-surface-3 px-1.5 py-px text-[0.6875rem] font-medium text-fg-muted">
                {t("modes.unavailable")}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs leading-snug text-fg-subtle">
            {unavailable && option.reason ? option.reason : option.description}
          </p>
        </div>
        {selected && <Check className="mt-1.5 size-4 shrink-0 text-accent-text" aria-hidden />}
      </div>
    );
  };

  return (
    <div ref={listRef} role="listbox" aria-label={t("modes.choose")} onKeyDown={onKeyDown} className="flex flex-col">
      <div role="group" aria-label={t("modes.modesGroup")} className="flex flex-col gap-0.5">
        <p className="px-2.5 pt-1.5 pb-1 text-xs font-medium text-fg-subtle" aria-hidden>
          {t("modes.modesGroup")}
        </p>
        {builtin.map(renderOption)}
      </div>
      {agents.length > 0 && (
        <div
          role="group"
          aria-label={t("modes.agentsGroup")}
          className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1"
        >
          <p className="px-2.5 pt-1.5 pb-1 text-xs font-medium text-fg-subtle" aria-hidden>
            {t("modes.agentsGroup")}
          </p>
          {agents.map(renderOption)}
        </div>
      )}
    </div>
  );
}
