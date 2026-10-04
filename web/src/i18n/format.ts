/** Locale-aware formatting helpers used across the UI. */
import type { Language, Translate } from "./index";

const LOCALES: Record<Language, string> = { en: "en-US", ru: "ru-RU" };

export function locale(language: Language): string {
  return LOCALES[language];
}

export function formatNumber(value: number, language: Language): string {
  return new Intl.NumberFormat(locale(language)).format(value);
}

export function formatBytes(bytes: number, t: Translate, language: Language): string {
  const units = [t("units.bytes"), t("units.kb"), t("units.mb"), t("units.gb")];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1;
  return `${new Intl.NumberFormat(locale(language), { maximumFractionDigits: digits }).format(value)} ${units[unit]}`;
}

/** Compact duration such as "42s", "3m 05s", "1h 20m". */
export function formatDuration(ms: number, t: Translate): string {
  if (ms < 1000) return t("units.underSecond");
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return t("units.hours", { h: hours, m: minutes });
  if (minutes > 0) return t("units.minutes", { m: minutes, s: String(seconds).padStart(2, "0") });
  return t("units.seconds", { s: seconds });
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Time for today's dates, otherwise a short date (with year when not the current year). */
export function formatShortDate(iso: string, language: Language, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  if (isSameDay(date, now)) {
    return new Intl.DateTimeFormat(locale(language), { hour: "2-digit", minute: "2-digit" }).format(date);
  }
  return new Intl.DateTimeFormat(locale(language), {
    day: "numeric",
    month: "short",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  }).format(date);
}

export function formatDateTime(iso: string, language: Language): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(locale(language), { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function isToday(iso: string, now: Date = new Date()): boolean {
  const date = new Date(iso);
  return !Number.isNaN(date.getTime()) && isSameDay(date, now);
}
