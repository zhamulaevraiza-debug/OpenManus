import { useState } from "react";

import { useAgents } from "@/api/queries";
import type { Settings } from "@/api/types";
import { Field, Input } from "@/components/Form";
import { Skeleton } from "@/components/Skeleton";
import { AgentTile } from "@/features/agents/AgentBadge";
import { agentDescription, agentName } from "@/features/agents/meta";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { SaveBar } from "./SaveBar";
import { Section } from "./Section";
import { useSaveSettings } from "./useSaveSettings";

export function AgentsTab({ settings }: { settings: Settings }) {
  const { t } = useI18n();
  const { data, isPending } = useAgents();
  const save = useSaveSettings();
  const initial = { planSteps: String(settings.team.max_plan_steps), maxSteps: String(settings.runtime.max_steps) };
  const [form, setForm] = useState(initial);
  const locked = (key: string) => settings.locked_by_env.includes(key);
  const planSteps = Number(form.planSteps);
  const maxSteps = Number(form.maxSteps);
  const valid = Number.isInteger(planSteps) && planSteps >= 1 && Number.isInteger(maxSteps) && maxSteps >= 1;
  const dirty = form.planSteps !== initial.planSteps || form.maxSteps !== initial.maxSteps;

  const persist = async () => {
    const result = await save.mutateAsync({
      ...(form.planSteps !== initial.planSteps ? { team: { max_plan_steps: planSteps } } : {}),
      ...(form.maxSteps !== initial.maxSteps ? { runtime: { max_steps: maxSteps } } : {}),
    });
    setForm({ planSteps: String(result.team.max_plan_steps), maxSteps: String(result.runtime.max_steps) });
  };

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("settings.agents.listTitle")} description={t("settings.agents.listHint")}>
        {isPending ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-14 w-full" />
            ))}
          </div>
        ) : (
          <ul className="-my-2 divide-y divide-border">
            {data?.agents.map((agent) => (
              <li key={agent.key} className="flex items-start gap-3 py-3">
                <AgentTile
                  agentKey={agent.key}
                  iconName={agent.icon}
                  className={cn("mt-0.5", !agent.available && "grayscale")}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-fg">{agentName(t, agent.key, agent.name)}</span>
                    <span
                      className={cn(
                        "rounded-full px-2 py-px text-[0.6875rem] font-medium",
                        agent.available ? "bg-success-soft text-success" : "bg-surface-3 text-fg-muted",
                      )}
                    >
                      {agent.available ? t("settings.agents.available") : t("settings.agents.unavailable")}
                    </span>
                    {agent.team_member && (
                      <span className="rounded-full bg-accent-soft px-2 py-px text-[0.6875rem] font-medium text-accent-text">
                        {t("settings.agents.teamMember")}
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-sm leading-relaxed text-fg-muted">
                    {agentDescription(t, agent.key, agent.description)}
                  </p>
                  {!agent.available && agent.reason && <p className="mt-1 text-xs text-warning">{agent.reason}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <div className="grid gap-6 md:grid-cols-2">
        <Section title={t("settings.agents.teamTitle")}>
          <Field
            label={t("settings.agents.maxPlanSteps")}
            hint={t("settings.agents.maxPlanStepsHint")}
            locked={locked("team.max_plan_steps")}
            lockedLabel={t("settings.lockedByEnv")}
          >
            {(props) => (
              <Input
                {...props}
                type="number"
                inputMode="numeric"
                min={1}
                max={50}
                value={form.planSteps}
                onChange={(event) => setForm({ ...form, planSteps: event.target.value })}
              />
            )}
          </Field>
        </Section>
        <Section title={t("settings.agents.runtimeTitle")}>
          <Field
            label={t("settings.agents.maxSteps")}
            hint={t("settings.agents.maxStepsHint")}
            locked={locked("runtime.max_steps")}
            lockedLabel={t("settings.lockedByEnv")}
          >
            {(props) => (
              <Input
                {...props}
                type="number"
                inputMode="numeric"
                min={1}
                max={500}
                value={form.maxSteps}
                onChange={(event) => setForm({ ...form, maxSteps: event.target.value })}
              />
            )}
          </Field>
        </Section>
      </div>
      <SaveBar
        dirty={dirty}
        saving={save.isPending}
        onSave={() => {
          if (valid) void persist().catch(() => undefined);
        }}
        onDiscard={() => setForm(initial)}
      />
    </div>
  );
}
