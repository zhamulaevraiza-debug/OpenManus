/** Tool presentation: icons, localized labels and one-line argument summaries. */
import {
  ChartColumn,
  Code,
  Eye,
  FilePenLine,
  Flag,
  Folder,
  Globe,
  ListChecks,
  MessageCircleQuestionMark,
  MessageSquare,
  Monitor,
  Plug,
  ScanText,
  Search,
  SquareTerminal,
  Table,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { Translate, TranslationKey } from "@/i18n";

const TOOL_ICONS: Record<string, LucideIcon> = {
  python_execute: Code,
  bash: SquareTerminal,
  browser_use: Globe,
  str_replace_editor: FilePenLine,
  web_search: Search,
  crawl4ai: ScanText,
  ask_human: MessageCircleQuestionMark,
  terminate: Flag,
  planning: ListChecks,
  create_chat_completion: MessageSquare,
  data_visualization: ChartColumn,
  visualization_preparation: Table,
  computer_use: Monitor,
  sandbox_browser: Globe,
  sandbox_files: Folder,
  sandbox_shell: SquareTerminal,
  sandbox_vision: Eye,
};

export function toolIcon(name: string): LucideIcon {
  if (TOOL_ICONS[name]) return TOOL_ICONS[name];
  return name.startsWith("mcp_") || name.includes("__") ? Plug : Wrench;
}

export function toolLabel(t: Translate, name: string): string {
  return name in TOOL_ICONS ? t(`tools.${name}` as TranslationKey) : name;
}

function firstLine(text: string): string {
  return (
    text
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line && !line.startsWith("#")) ?? text.trim()
  );
}

function asText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) return value.join(", ");
  return null;
}

const MAX_SUMMARY = 140;

function clip(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > MAX_SUMMARY ? `${flat.slice(0, MAX_SUMMARY - 1)}…` : flat;
}

/** A compact, human-readable summary of a tool call's arguments. */
export function summarizeArguments(name: string, args: Record<string, unknown>): string {
  const pick = (...keys: string[]) => {
    for (const key of keys) {
      const value = asText(args[key]);
      if (value) return value;
    }
    return null;
  };
  let summary: string | null = null;
  switch (name) {
    case "python_execute":
      summary = pick("code") ? firstLine(pick("code") as string) : null;
      break;
    case "bash":
    case "sandbox_shell":
      summary = pick("command", "cmd");
      break;
    case "web_search":
      summary = pick("query");
      break;
    case "browser_use":
    case "sandbox_browser": {
      const action = pick("action");
      const target = pick("url", "query", "goal", "text", "keys", "index");
      summary = [action, target].filter(Boolean).join(" · ") || null;
      break;
    }
    case "str_replace_editor":
    case "sandbox_files": {
      const command = pick("command", "action");
      const path = pick("path", "file_path");
      summary = [command, path].filter(Boolean).join(" · ") || null;
      break;
    }
    case "crawl4ai":
      summary = pick("urls", "url");
      break;
    case "ask_human":
      summary = pick("inquire", "question");
      break;
    case "terminate":
      summary = pick("status");
      break;
    case "planning":
      summary = [pick("command"), pick("title")].filter(Boolean).join(" · ") || null;
      break;
    default:
      break;
  }
  if (!summary) {
    const firstValue = Object.values(args).map(asText).find(Boolean);
    summary = firstValue ?? (Object.keys(args).length ? JSON.stringify(args) : "");
  }
  return clip(summary);
}

/** Argument that is best shown as a code block (with its language), if any. */
export function codeArgument(name: string, args: Record<string, unknown>): { code: string; language: string } | null {
  if ((name === "python_execute" || name === "visualization_preparation") && typeof args.code === "string") {
    return { code: args.code, language: "python" };
  }
  if ((name === "bash" || name === "sandbox_shell") && typeof args.command === "string") {
    return { code: args.command, language: "bash" };
  }
  return null;
}
