import { createElement } from "react";

import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { agentIcon, agentLabel, agentTone } from "./meta";

interface AgentBadgeProps {
  agentKey: string | null;
  title?: string | null;
  iconName?: string | null;
  size?: "sm" | "md";
  className?: string;
}

/** Coloured chip with the agent's icon and localized name. */
export function AgentBadge({ agentKey, title, iconName, size = "sm", className }: AgentBadgeProps) {
  const { t } = useI18n();
  const tone = agentTone(agentKey);
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-full font-medium whitespace-nowrap",
        size === "sm" ? "h-6 px-2 text-xs [&_svg]:size-3.5" : "h-7 px-2.5 text-[0.8125rem] [&_svg]:size-4",
        tone.chip,
        className,
      )}
    >
      <AgentIcon agentKey={agentKey} iconName={iconName} className="shrink-0" />
      <span className="truncate">{agentLabel(t, agentKey, title)}</span>
    </span>
  );
}

/** Square icon tile used in pickers and lists. */
export function AgentTile({
  agentKey,
  iconName,
  className,
}: {
  agentKey: string;
  iconName?: string | null;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-lg shadow-xs [&_svg]:size-4",
        agentTone(agentKey).tile,
        className,
      )}
    >
      <AgentIcon agentKey={agentKey} iconName={iconName} />
    </span>
  );
}

/** The lucide icon of an agent or mode. */
export function AgentIcon({
  agentKey,
  iconName,
  className,
}: {
  agentKey: string | null;
  iconName?: string | null;
  className?: string;
}) {
  return createElement(agentIcon(agentKey, iconName), { className, "aria-hidden": true });
}
