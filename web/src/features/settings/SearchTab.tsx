import { useState } from "react";

import type { Settings } from "@/api/types";
import { Field, Input, Select, Switch } from "@/components/Form";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { SaveBar } from "./SaveBar";
import { Section } from "./Section";
import { changedFields, useSaveSettings } from "./useSaveSettings";

const ENGINES = ["Google", "DuckDuckGo", "Bing", "Baidu"];

interface SearchForm {
  engine: string;
  fallback_engines: string[];
  lang: string;
  country: string;
  headless: boolean;
}

function toForm(settings: Settings): SearchForm {
  return { ...settings.search, headless: settings.browser.headless };
}

export function SearchTab({ settings }: { settings: Settings }) {
  const { t } = useI18n();
  const save = useSaveSettings();
  const [form, setForm] = useState(() => toForm(settings));
  const initial = toForm(settings);
  const locked = (key: string) => settings.locked_by_env.includes(key);
  const lockedHint = t("settings.lockedByEnv");

  const { headless, ...search } = form;
  const { headless: initialHeadless, ...initialSearch } = initial;
  const searchChanges = changedFields(initialSearch, search);
  const dirty = Object.keys(searchChanges).length > 0 || headless !== initialHeadless;
  const engines = ENGINES.includes(form.engine) ? ENGINES : [form.engine, ...ENGINES];

  const persist = async () => {
    const result = await save.mutateAsync({
      ...(Object.keys(searchChanges).length ? { search: searchChanges } : {}),
      ...(headless !== initialHeadless ? { browser: { headless } } : {}),
    });
    setForm(toForm(result));
  };

  const toggleFallback = (engine: string) =>
    setForm((current) => ({
      ...current,
      fallback_engines: current.fallback_engines.includes(engine)
        ? current.fallback_engines.filter((item) => item !== engine)
        : [...current.fallback_engines, engine],
    }));

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("settings.search.searchTitle")}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("settings.search.engine")} locked={locked("search.engine")} lockedLabel={lockedHint}>
            {(props) => (
              <Select
                {...props}
                value={form.engine}
                onChange={(event) => setForm({ ...form, engine: event.target.value })}
              >
                {engines.map((engine) => (
                  <option key={engine} value={engine}>
                    {engine}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-fg">{t("settings.search.fallback")}</span>
            <div className="flex flex-wrap gap-2" role="group" aria-label={t("settings.search.fallback")}>
              {ENGINES.filter((engine) => engine !== form.engine).map((engine) => {
                const checked = form.fallback_engines.includes(engine);
                return (
                  <button
                    key={engine}
                    type="button"
                    role="checkbox"
                    aria-checked={checked}
                    disabled={locked("search.fallback_engines")}
                    onClick={() => toggleFallback(engine)}
                    className={cn(
                      "h-10 rounded-full border px-3.5 text-sm font-medium transition-colors disabled:opacity-50 max-md:h-11",
                      checked
                        ? "border-accent bg-accent-soft text-accent-text"
                        : "border-border text-fg-muted hover:bg-surface-2",
                    )}
                  >
                    {engine}
                  </button>
                );
              })}
            </div>
          </div>
          <Field label={t("settings.search.lang")} locked={locked("search.lang")} lockedLabel={lockedHint}>
            {(props) => (
              <Input
                {...props}
                value={form.lang}
                maxLength={8}
                placeholder="en"
                onChange={(event) => setForm({ ...form, lang: event.target.value })}
              />
            )}
          </Field>
          <Field label={t("settings.search.country")} locked={locked("search.country")} lockedLabel={lockedHint}>
            {(props) => (
              <Input
                {...props}
                value={form.country}
                maxLength={8}
                placeholder="us"
                onChange={(event) => setForm({ ...form, country: event.target.value })}
              />
            )}
          </Field>
        </div>
      </Section>
      <Section title={t("settings.search.browserTitle")}>
        <Switch
          checked={form.headless}
          disabled={locked("browser.headless")}
          onChange={(value) => setForm({ ...form, headless: value })}
          label={t("settings.search.headless")}
          description={locked("browser.headless") ? lockedHint : t("settings.search.headlessHint")}
        />
      </Section>
      <SaveBar
        dirty={dirty}
        saving={save.isPending}
        onSave={() => void persist().catch(() => undefined)}
        onDiscard={() => setForm(initial)}
      />
    </div>
  );
}
