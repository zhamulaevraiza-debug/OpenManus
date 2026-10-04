/** Presentation metadata for agents and modes: icons, colours and localized labels. */
import {
  BookOpen,
  Bot,
  Box,
  Brain,
  ChartColumn,
  Code,
  Database,
  FileText,
  Globe,
  MessageCircle,
  PenLine,
  Search,
  Sparkles,
  SquareTerminal,
  Telescope,
  Users,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { AgentInfo } from "@/api/types";
import type { Translate, TranslationKey } from "@/i18n";

/** Icons by lucide name, as sent by the API (`AgentInfo.icon`). */
const ICONS_BY_NAME: Record<string, LucideIcon> = {
  bot: Bot,
  globe: Globe,
  search: Search,
  telescope: Telescope,
  code: Code,
  "bar-chart-3": ChartColumn,
  "chart-column": ChartColumn,
  "pen-line": PenLine,
  box: Box,
  brain: Brain,
  terminal: SquareTerminal,
  database: Database,
  "file-text": FileText,
  "book-open": BookOpen,
  wrench: Wrench,
  sparkles: Sparkles,
  users: Users,
};

const ICONS_BY_KEY: Record<string, LucideIcon> = {
  auto: Sparkles,
  chat: MessageCircle,
  team: Users,
  manus: Bot,
  browser: Globe,
  researcher: Telescope,
  coder: Code,
  data_analyst: ChartColumn,
  writer: PenLine,
  sandbox: Box,
};

export function agentIcon(key: string | null | undefined, iconName?: string | null): LucideIcon {
  if (key && ICONS_BY_KEY[key]) return ICONS_BY_KEY[key];
  if (iconName && ICONS_BY_NAME[iconName]) return ICONS_BY_NAME[iconName];
  return Bot;
}

export interface Tone {
  /** Soft chip: background + text. */
  chip: string;
  /** Solid icon tile. */
  tile: string;
  /** Small status dot / accent bar. */
  dot: string;
}

const TONES: Record<string, Tone> = {
  manus: {
    chip: "bg-indigo-500/10 text-indigo-700 dark:bg-indigo-400/15 dark:text-indigo-300",
    tile: "bg-indigo-500 text-white",
    dot: "bg-indigo-500",
  },
  browser: {
    chip: "bg-sky-500/10 text-sky-700 dark:bg-sky-400/15 dark:text-sky-300",
    tile: "bg-sky-500 text-white",
    dot: "bg-sky-500",
  },
  researcher: {
    chip: "bg-amber-500/12 text-amber-800 dark:bg-amber-400/15 dark:text-amber-300",
    tile: "bg-amber-500 text-white",
    dot: "bg-amber-500",
  },
  coder: {
    chip: "bg-emerald-500/10 text-emerald-700 dark:bg-emerald-400/15 dark:text-emerald-300",
    tile: "bg-emerald-600 text-white",
    dot: "bg-emerald-500",
  },
  data_analyst: {
    chip: "bg-violet-500/10 text-violet-700 dark:bg-violet-400/15 dark:text-violet-300",
    tile: "bg-violet-500 text-white",
    dot: "bg-violet-500",
  },
  writer: {
    chip: "bg-rose-500/10 text-rose-700 dark:bg-rose-400/15 dark:text-rose-300",
    tile: "bg-rose-500 text-white",
    dot: "bg-rose-500",
  },
  sandbox: {
    chip: "bg-orange-500/10 text-orange-700 dark:bg-orange-400/15 dark:text-orange-300",
    tile: "bg-orange-500 text-white",
    dot: "bg-orange-500",
  },
  team: {
    chip: "bg-teal-500/10 text-teal-700 dark:bg-teal-400/15 dark:text-teal-300",
    tile: "bg-teal-500 text-white",
    dot: "bg-teal-500",
  },
  auto: {
    chip: "bg-accent-soft text-accent-text",
    tile: "bg-gradient-to-br from-indigo-500 to-purple-600 text-white",
    dot: "bg-accent",
  },
  chat: {
    chip: "bg-zinc-500/10 text-zinc-700 dark:bg-zinc-400/15 dark:text-zinc-300",
    tile: "bg-zinc-600 text-white dark:bg-zinc-500",
    dot: "bg-zinc-500",
  },
};

const EXTRA_TONES: Tone[] = [
  {
    chip: "bg-cyan-500/10 text-cyan-700 dark:bg-cyan-400/15 dark:text-cyan-300",
    tile: "bg-cyan-600 text-white",
    dot: "bg-cyan-500",
  },
  {
    chip: "bg-lime-500/12 text-lime-800 dark:bg-lime-400/15 dark:text-lime-300",
    tile: "bg-lime-600 text-white",
    dot: "bg-lime-500",
  },
  {
    chip: "bg-pink-500/10 text-pink-700 dark:bg-pink-400/15 dark:text-pink-300",
    tile: "bg-pink-500 text-white",
    dot: "bg-pink-500",
  },
  {
    chip: "bg-fuchsia-500/10 text-fuchsia-700 dark:bg-fuchsia-400/15 dark:text-fuchsia-300",
    tile: "bg-fuchsia-500 text-white",
    dot: "bg-fuchsia-500",
  },
];

export function agentTone(key: string | null | undefined): Tone {
  if (!key) return TONES.chat;
  if (TONES[key]) return TONES[key];
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return EXTRA_TONES[hash % EXTRA_TONES.length];
}

const BUILTIN_MODES = new Set(["auto", "chat", "team"]);
const KNOWN_AGENTS = new Set(["manus", "browser", "researcher", "coder", "data_analyst", "writer", "sandbox"]);

/** Localized display name for a mode/agent key, falling back to the API text or the key itself. */
export function agentName(t: Translate, key: string, fallback?: string | null): string {
  if (BUILTIN_MODES.has(key)) return t(`modes.${key}.name` as TranslationKey);
  if (KNOWN_AGENTS.has(key)) return t(`agents.${key}.name` as TranslationKey);
  return fallback || key;
}

export function agentDescription(t: Translate, key: string, fallback?: string | null): string {
  if (BUILTIN_MODES.has(key)) return t(`modes.${key}.description` as TranslationKey);
  if (KNOWN_AGENTS.has(key)) return t(`agents.${key}.description` as TranslationKey);
  return fallback ?? "";
}

export function isBuiltinMode(key: string): boolean {
  return BUILTIN_MODES.has(key);
}

/** Suggestion prompts for the empty state of a mode (falls back to Auto's). */
export function suggestionKeys(mode: string): TranslationKey[] {
  const group = BUILTIN_MODES.has(mode) || KNOWN_AGENTS.has(mode) ? mode : "auto";
  return (["a", "b", "c", "d"] as const).map((id) => `suggestions.${group}.${id}` as TranslationKey);
}

export function findAgent(agents: readonly AgentInfo[] | undefined, key: string | null): AgentInfo | undefined {
  return key ? agents?.find((agent) => agent.key === key) : undefined;
}

/** Name for an event's agent: localized by key, else the title the backend sent. */
export function agentLabel(t: Translate, key: string | null, title?: string | null): string {
  return key ? agentName(t, key, title) : (title ?? "");
}
