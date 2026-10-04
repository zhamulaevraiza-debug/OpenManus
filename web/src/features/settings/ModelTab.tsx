import { useMutation } from "@tanstack/react-query";
import { CircleCheck, CircleX, PlugZap, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import type { LLMSettingsUpdate, LLMSettingsView, Settings } from "@/api/types";
import { Button } from "@/components/Button";
import { Field, Input, Select } from "@/components/Form";
import { ConfirmDialog } from "@/components/Modal";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";
import { errorMessage } from "@/utils/errors";

import { PROVIDER_PRESETS } from "./presets";
import { SaveBar } from "./SaveBar";
import { Section } from "./Section";
import { changedFields, useSaveSettings } from "./useSaveSettings";

type Which = "llm" | "llm_vision";

interface LlmFormState {
  api_type: string;
  base_url: string;
  model: string;
  api_key: string;
  max_tokens: string;
  temperature: string;
  api_version: string;
  supports_images: "auto" | "yes" | "no";
}

const KNOWN_API_TYPES = new Set(["azure", "aws", "ollama"]);

function toForm(view: LLMSettingsView): LlmFormState {
  return {
    api_type: KNOWN_API_TYPES.has(view.api_type) ? view.api_type : "",
    base_url: view.base_url,
    model: view.model,
    api_key: "",
    max_tokens: String(view.max_tokens),
    temperature: String(view.temperature),
    api_version: view.api_version,
    supports_images: view.supports_images === null ? "auto" : view.supports_images ? "yes" : "no",
  };
}

function toUpdate(form: LlmFormState): LLMSettingsUpdate {
  const update: LLMSettingsUpdate = {
    api_type: form.api_type,
    base_url: form.base_url.trim(),
    model: form.model.trim(),
    max_tokens: Number(form.max_tokens),
    temperature: Number(form.temperature),
    api_version: form.api_version.trim(),
    supports_images: form.supports_images === "auto" ? null : form.supports_images === "yes",
  };
  if (form.api_key.trim()) update.api_key = form.api_key.trim();
  return update;
}

export function ModelTab({ settings }: { settings: Settings }) {
  const { t } = useI18n();
  const save = useSaveSettings();
  const [addingVision, setAddingVision] = useState(false);
  const [removeVision, setRemoveVision] = useState(false);
  const vision = settings.llm_vision;

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("settings.model.mainTitle")} description={t("settings.model.mainHint")}>
        <LlmForm which="llm" view={settings.llm} locked={settings.locked_by_env} />
      </Section>
      <Section
        title={t("settings.model.visionTitle")}
        description={t("settings.model.visionHint")}
        actions={
          vision ? (
            <Button
              variant="ghost"
              size="sm"
              icon={<Trash2 className="size-4" />}
              onClick={() => setRemoveVision(true)}
            >
              {t("common.remove")}
            </Button>
          ) : null
        }
      >
        {vision || addingVision ? (
          <LlmForm
            which="llm_vision"
            view={vision ?? { ...settings.llm, api_key_set: false, api_key_hint: null, supports_images: true }}
            locked={settings.locked_by_env}
            isNew={!vision}
            onCancelNew={() => setAddingVision(false)}
          />
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-fg-muted">{t("settings.model.visionNotSet")}</p>
            <Button variant="outline" icon={<Plus className="size-4" />} onClick={() => setAddingVision(true)}>
              {t("settings.model.addVision")}
            </Button>
          </div>
        )}
      </Section>
      <ConfirmDialog
        open={removeVision}
        title={t("settings.model.removeVision")}
        confirmLabel={t("common.remove")}
        danger
        onConfirm={() => save.mutateAsync({ llm_vision: null }).then(() => setAddingVision(false))}
        onClose={() => setRemoveVision(false)}
      />
    </div>
  );
}

interface LlmFormProps {
  which: Which;
  view: LLMSettingsView;
  locked: string[];
  isNew?: boolean;
  onCancelNew?: () => void;
}

function LlmForm({ which, view, locked, isNew = false, onCancelNew }: LlmFormProps) {
  const { t } = useI18n();
  const save = useSaveSettings();
  const initial = toForm(view);
  const [form, setForm] = useState<LlmFormState>(initial);
  const test = useMutation({ mutationFn: () => api.testLlm(which) });
  const isLocked = (field: string) => locked.includes(`${which}.${field}`);
  const set = <K extends keyof LlmFormState>(key: K, value: LlmFormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    test.reset();
  };

  const baseUpdate = toUpdate(initial);
  const nextUpdate = toUpdate(form);
  const changes = isNew ? nextUpdate : changedFields(baseUpdate, nextUpdate);
  for (const field of Object.keys(changes)) {
    if (isLocked(field)) delete changes[field as keyof LLMSettingsUpdate];
  }
  const dirty = isNew || Object.keys(changes).length > 0;
  const invalid =
    !form.model.trim() ||
    !Number.isFinite(Number(form.max_tokens)) ||
    Number(form.max_tokens) <= 0 ||
    !Number.isFinite(Number(form.temperature));

  const persist = async () => {
    const result = await save.mutateAsync({ [which]: changes });
    const updated = result[which];
    if (updated) setForm(toForm(updated));
    return result;
  };

  const saveAndTest = async () => {
    try {
      if (dirty) await persist();
      await test.mutateAsync();
    } catch {
      // errors are surfaced by the mutations
    }
  };

  const applyPreset = (presetId: string) => {
    const preset = PROVIDER_PRESETS.find((item) => item.id === presetId);
    if (!preset) return;
    setForm((current) => ({
      ...current,
      api_type: isLocked("api_type") ? current.api_type : preset.apiType,
      base_url: isLocked("base_url") ? current.base_url : preset.baseUrl,
      model: isLocked("model") ? current.model : preset.model,
      api_version: isLocked("api_version") ? current.api_version : (preset.apiVersion ?? current.api_version),
    }));
    test.reset();
  };

  const lockedHint = t("settings.lockedByEnv");

  return (
    <div className="flex flex-col gap-5">
      <div>
        <p className="mb-2 text-sm font-medium text-fg">{t("settings.model.presets")}</p>
        <div className="flex flex-wrap gap-2">
          {PROVIDER_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => applyPreset(preset.id)}
              className={cn(
                "h-9 rounded-full border px-3.5 text-sm font-medium transition-colors max-md:h-10",
                form.base_url === preset.baseUrl && form.api_type === preset.apiType
                  ? "border-accent bg-accent-soft text-accent-text"
                  : "border-border text-fg-muted hover:bg-surface-2 hover:text-fg",
              )}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("settings.model.apiType")} locked={isLocked("api_type")} lockedLabel={lockedHint}>
          {(props) => (
            <Select {...props} value={form.api_type} onChange={(event) => set("api_type", event.target.value)}>
              <option value="">{t("settings.model.apiTypes.openai")}</option>
              <option value="azure">{t("settings.model.apiTypes.azure")}</option>
              <option value="aws">{t("settings.model.apiTypes.aws")}</option>
              <option value="ollama">{t("settings.model.apiTypes.ollama")}</option>
            </Select>
          )}
        </Field>
        <Field label={t("settings.model.modelName")} locked={isLocked("model")} lockedLabel={lockedHint}>
          {(props) => (
            <Input
              {...props}
              value={form.model}
              onChange={(event) => set("model", event.target.value)}
              spellCheck={false}
            />
          )}
        </Field>
        <Field
          label={t("settings.model.baseUrl")}
          locked={isLocked("base_url")}
          lockedLabel={lockedHint}
          className="sm:col-span-2"
        >
          {(props) => (
            <Input
              {...props}
              type="url"
              inputMode="url"
              value={form.base_url}
              onChange={(event) => set("base_url", event.target.value)}
              spellCheck={false}
              placeholder="https://api.openai.com/v1"
            />
          )}
        </Field>
        <Field
          label={t("settings.model.apiKey")}
          locked={isLocked("api_key")}
          lockedLabel={lockedHint}
          hint={
            view.api_key_set
              ? t("settings.model.apiKeySaved", { hint: view.api_key_hint ?? "••••" })
              : t("settings.model.apiKeyMissing")
          }
          className="sm:col-span-2"
        >
          {(props) => (
            <Input
              {...props}
              type="password"
              autoComplete="off"
              value={form.api_key}
              onChange={(event) => set("api_key", event.target.value)}
              placeholder={view.api_key_set ? `${view.api_key_hint ?? "••••"}` : t("settings.model.apiKeyPlaceholder")}
            />
          )}
        </Field>
        <Field label={t("settings.model.maxTokens")} locked={isLocked("max_tokens")} lockedLabel={lockedHint}>
          {(props) => (
            <Input
              {...props}
              type="number"
              inputMode="numeric"
              min={1}
              value={form.max_tokens}
              onChange={(event) => set("max_tokens", event.target.value)}
            />
          )}
        </Field>
        <Field label={t("settings.model.temperature")} locked={isLocked("temperature")} lockedLabel={lockedHint}>
          {(props) => (
            <Input
              {...props}
              type="number"
              inputMode="decimal"
              min={0}
              max={2}
              step={0.1}
              value={form.temperature}
              onChange={(event) => set("temperature", event.target.value)}
            />
          )}
        </Field>
        <Field
          label={t("settings.model.apiVersion")}
          hint={t("settings.model.apiVersionHint")}
          locked={isLocked("api_version")}
          lockedLabel={lockedHint}
        >
          {(props) => (
            <Input
              {...props}
              value={form.api_version}
              onChange={(event) => set("api_version", event.target.value)}
              spellCheck={false}
            />
          )}
        </Field>
        <Field label={t("settings.model.supportsImages")} locked={isLocked("supports_images")} lockedLabel={lockedHint}>
          {(props) => (
            <Select
              {...props}
              value={form.supports_images}
              onChange={(event) => set("supports_images", event.target.value as LlmFormState["supports_images"])}
            >
              <option value="auto">{t("settings.model.imagesAuto")}</option>
              <option value="yes">{t("settings.model.imagesYes")}</option>
              <option value="no">{t("settings.model.imagesNo")}</option>
            </Select>
          )}
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
        <Button
          variant="outline"
          icon={<PlugZap className="size-4" />}
          loading={test.isPending}
          disabled={invalid || save.isPending}
          onClick={saveAndTest}
        >
          {test.isPending
            ? t("settings.model.testing")
            : dirty
              ? t("settings.model.saveAndTest")
              : t("settings.model.test")}
        </Button>
        {test.data && (
          <span
            role="status"
            className={cn("flex min-w-0 items-center gap-1.5 text-sm", test.data.ok ? "text-success" : "text-danger")}
          >
            {test.data.ok ? <CircleCheck className="size-4 shrink-0" /> : <CircleX className="size-4 shrink-0" />}
            <span className="min-w-0 break-words">
              {test.data.ok
                ? t("settings.model.testOk", { ms: Math.round(test.data.latency_ms) })
                : t("settings.model.testFailed")}
              {!test.data.ok && test.data.message ? ` — ${test.data.message}` : ""}
            </span>
          </span>
        )}
        {test.error && (
          <span role="status" className="flex items-center gap-1.5 text-sm text-danger">
            <CircleX className="size-4 shrink-0" />
            {errorMessage(t, test.error, "settings.model.testFailed")}
          </span>
        )}
        {isNew && (
          <div className="ml-auto flex gap-2">
            <Button variant="ghost" onClick={onCancelNew}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="primary"
              loading={save.isPending}
              disabled={invalid}
              onClick={() => void persist().catch(() => undefined)}
            >
              {t("common.save")}
            </Button>
          </div>
        )}
      </div>

      {!isNew && (
        <SaveBar
          dirty={dirty}
          saving={save.isPending}
          onSave={() => {
            if (!invalid) void persist().catch(() => undefined);
          }}
          onDiscard={() => {
            setForm(initial);
            test.reset();
          }}
        />
      )}
    </div>
  );
}
