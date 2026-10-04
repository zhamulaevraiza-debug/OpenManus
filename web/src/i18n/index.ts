/**
 * Minimal typed i18n: dictionaries keyed by dot paths, `{name}` interpolation and CLDR plural forms.
 */
import { createContext, useContext } from "react";

import type { en } from "./en";

export type Language = "en" | "ru";
export const LANGUAGES: readonly Language[] = ["en", "ru"];
export const LANGUAGE_NAMES: Record<Language, string> = { en: "English", ru: "Русский" };

export interface PluralForms {
  one: string;
  few: string;
  many: string;
  other: string;
}

type DeepStrings<T> = {
  [K in keyof T]: T[K] extends string ? string : T[K] extends PluralForms ? PluralForms : DeepStrings<T[K]>;
};

/** Shape every dictionary must have (derived from the English one). */
export type Dictionary = DeepStrings<typeof en>;

type Paths<T> = {
  [K in keyof T & string]: T[K] extends string ? K : T[K] extends PluralForms ? K : `${K}.${Paths<T[K]>}`;
}[keyof T & string];

export type TranslationKey = Paths<Dictionary>;
export type TranslationVars = Record<string, string | number>;
export type Translate = (key: TranslationKey, vars?: TranslationVars) => string;

function isPlural(value: unknown): value is PluralForms {
  return Boolean(value) && typeof value === "object" && "other" in (value as object) && "one" in (value as object);
}

function lookup(dictionary: Dictionary, key: string): unknown {
  let node: unknown = dictionary;
  for (const part of key.split(".")) {
    if (!node || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

/** Translates `key`; plural entries pick a form from `vars.count`. Missing keys render as the key. */
export function translate(dictionary: Dictionary, language: Language, key: string, vars?: TranslationVars): string {
  let value = lookup(dictionary, key);
  if (isPlural(value)) {
    const count = Number(vars?.count ?? 0);
    const form = new Intl.PluralRules(language).select(count) as keyof PluralForms | "zero" | "two";
    value = form in value ? value[form as keyof PluralForms] : value.other;
  }
  if (typeof value !== "string") return key;
  if (!vars) return value;
  return value.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match));
}

/** Initial language: persisted choice, else Russian for ru-* browsers, else English. */
export function detectLanguage(): Language {
  try {
    const stored = localStorage.getItem("om.lang");
    if (stored === "en" || stored === "ru") return stored;
  } catch {
    // storage unavailable
  }
  const preferred = typeof navigator !== "undefined" ? navigator.language : "";
  return preferred.toLowerCase().startsWith("ru") ? "ru" : "en";
}

export interface I18nValue {
  language: Language;
  setLanguage: (language: Language) => void;
  t: Translate;
}

export const I18nContext = createContext<I18nValue | null>(null);

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}
