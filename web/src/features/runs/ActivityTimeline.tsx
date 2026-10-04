import {
  Ban,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleAlert,
  CircleX,
  Hourglass,
  Lightbulb,
  MessageCircleQuestionMark,
  Monitor,
  TriangleAlert,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import type { Run } from "@/api/types";
import { Skeleton } from "@/components/Skeleton";
import { Spinner } from "@/components/Spinner";
import { AgentBadge } from "@/features/agents/AgentBadge";
import { useNow } from "@/hooks/useNow";
import { useI18n, type TranslationKey } from "@/i18n";
import { formatDuration } from "@/i18n/format";
import { cn } from "@/utils/cn";

import {
  countTools,
  isTerminalStatus,
  planProgress,
  type ActivityGroup,
  type ActivityItem,
  type PlanStep,
  type PlanView,
  type RunView,
} from "./reducer";
import { ToolCallCard } from "./ToolCallCard";

const STATUS_LABEL: Record<Run["status"], TranslationKey> = {
  queued: "activity.queued",
  running: "activity.working",
  waiting_input: "activity.waitingInput",
  completed: "activity.completed",
  failed: "activity.failed",
  cancelled: "activity.cancelled",
};

function StatusIcon({ status }: { status: Run["status"] }) {
  switch (status) {
    case "running":
      return <Spinner className="size-4 text-accent-text" />;
    case "queued":
      return <Hourglass className="size-4 text-fg-subtle" aria-hidden />;
    case "waiting_input":
      return <MessageCircleQuestionMark className="size-4 text-warning" aria-hidden />;
    case "completed":
      return (
        <span className="inline-flex size-4 items-center justify-center rounded-full bg-success text-white dark:text-zinc-950">
          <Check className="size-3" strokeWidth={3} aria-hidden />
        </span>
      );
    case "failed":
      return <CircleX className="size-4 text-danger" aria-hidden />;
    case "cancelled":
      return <Ban className="size-4 text-fg-subtle" aria-hidden />;
  }
}

function elapsedMs(view: RunView | undefined, run: Run, now: number): number | null {
  if (view?.durationMs != null) return view.durationMs;
  const start = view?.startedAt ?? run.started_at;
  if (!start) return null;
  const end = view?.finishedAt ?? run.finished_at;
  return Math.max(0, (end ? Date.parse(end) : now) - Date.parse(start));
}

interface RunActivityProps {
  run: Run;
  view: RunView | undefined;
  loading: boolean;
  expanded: boolean;
  onToggle: () => void;
}

/** Collapsible activity card: status, route, plan progress, elapsed time and the event timeline. */
export function RunActivity({ run, view, loading, expanded, onToggle }: RunActivityProps) {
  const { t } = useI18n();
  const status = view && !isTerminalStatus(run.status) ? view.status : run.status;
  const active = !isTerminalStatus(status);
  const now = useNow(active);
  const elapsed = elapsedMs(view, run, now);
  const progress = planProgress(view?.plan ?? null);
  const tools = view ? countTools(view) : 0;
  const route = view?.route ?? null;
  // Before the events are loaded (collapsed finished runs) fall back to the mode the run was started with.
  const requestedMode = run.mode !== "auto" ? run.mode : null;
  const routeAgent = route
    ? route.mode === "team"
      ? "team"
      : (route.agent ?? (route.mode === "chat" ? "chat" : null))
    : requestedMode;

  return (
    <section
      data-testid="activity"
      data-status={status}
      aria-label={t("activity.title")}
      className={cn(
        "overflow-hidden rounded-2xl border bg-surface shadow-xs",
        active ? "border-accent/30" : "border-border",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-label={expanded ? t("activity.hide") : t("activity.show")}
        className="flex min-h-12 w-full flex-wrap items-center gap-x-2.5 gap-y-1.5 px-3.5 py-2.5 text-left transition-colors hover:bg-surface-2/60"
      >
        <span className="flex min-w-0 flex-1 items-center gap-2.5">
          <StatusIcon status={status} />
          <span className={cn("text-sm font-medium", active ? "text-fg" : "text-fg-muted")}>
            {t(STATUS_LABEL[status])}
          </span>
          {routeAgent && <AgentBadge agentKey={routeAgent} className="max-sm:hidden" />}
          {progress.total > 0 && (
            <span className="flex items-center gap-1.5 text-xs text-fg-subtle tabular-nums">
              <span className="relative h-1.5 w-12 overflow-hidden rounded-full bg-surface-3" aria-hidden>
                <span
                  className="absolute inset-y-0 left-0 rounded-full bg-accent transition-[width] duration-500"
                  style={{ width: `${(progress.done / progress.total) * 100}%` }}
                />
              </span>
              {t("activity.planProgress", { done: progress.done, total: progress.total })}
            </span>
          )}
          {tools > 0 && !progress.total && (
            <span className="truncate text-xs text-fg-subtle max-sm:hidden">
              {t("activity.toolCalls", { count: tools })}
            </span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-2 text-xs text-fg-subtle tabular-nums">
          {elapsed !== null && <span>{formatDuration(elapsed, t)}</span>}
          <ChevronDown
            className={cn("size-4 transition-transform duration-200", expanded && "rotate-180")}
            aria-hidden
          />
        </span>
      </button>

      {expanded && (loading || hasDetails(view)) && (
        <div className="border-t border-border px-3 pt-3 pb-3 sm:px-3.5 sm:pb-3.5">
          {loading && !view?.groups.length ? (
            <div className="flex flex-col gap-2.5" aria-busy="true">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : (
            view && <ActivityBody view={view} routed={run.mode === "auto"} />
          )}
        </div>
      )}
    </section>
  );
}

function hasDetails(view: RunView | undefined): boolean {
  // A route straight to chat says nothing beyond the header badge.
  const informativeRoute = view?.route && view.route.mode !== "chat";
  return Boolean(view && (informativeRoute || view.plan || view.groups.length > 0 || view.sandbox));
}

/** `routed`: the router chose the mode (Auto), so its reason is worth showing. */
function ActivityBody({ view, routed }: { view: RunView; routed: boolean }) {
  const { t } = useI18n();
  const route = view.route;
  const planGroups = view.groups.filter((group) => group.kind === "plan_step");
  const otherGroups = view.plan ? view.groups.filter((group) => group.kind !== "plan_step") : view.groups;
  // The open question is shown in the answer card below the activity; don't repeat it here.
  const pendingId = view.pendingQuestion?.question_id ?? null;

  return (
    <div className="flex flex-col gap-3.5">
      {route && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-fg-muted">
          {route.mode === "team" ? (
            <AgentBadge agentKey="team" />
          ) : route.mode === "chat" ? (
            <AgentBadge agentKey="chat" />
          ) : (
            <AgentBadge agentKey={route.agent ?? "manus"} />
          )}
          {routed && route.reason && (
            <span className="min-w-0 flex-1 text-xs leading-relaxed text-fg-subtle">{route.reason}</span>
          )}
        </div>
      )}
      {view.plan && <PlanChecklist plan={view.plan} groups={planGroups} pendingQuestionId={pendingId} />}
      {otherGroups.map((group) => (
        <GroupView key={group.id} group={group} pendingQuestionId={pendingId} />
      ))}
      {view.sandbox && (view.sandbox.vncUrl || view.sandbox.websiteUrl) && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl bg-surface-2 px-3 py-2 text-sm">
          <Monitor className="size-4 text-fg-subtle" aria-hidden />
          <span className="font-medium">{t("activity.sandboxReady")}</span>
          {view.sandbox.vncUrl && (
            <a
              href={view.sandbox.vncUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent-text hover:underline"
            >
              {t("activity.openDesktop")}
            </a>
          )}
          {view.sandbox.websiteUrl && (
            <a
              href={view.sandbox.websiteUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent-text hover:underline"
            >
              {t("activity.openWebsite")}
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function PlanStepIcon({ status }: { status: PlanStep["status"] }) {
  if (status === "completed") {
    return (
      <span className="inline-flex size-5 items-center justify-center rounded-full bg-success text-white dark:text-zinc-950">
        <Check className="size-3" strokeWidth={3} aria-hidden />
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span className="inline-flex size-5 items-center justify-center rounded-full bg-accent-soft">
        <Spinner className="size-3.5 text-accent-text" />
      </span>
    );
  }
  if (status === "blocked") return <CircleAlert className="size-5 text-warning" aria-hidden />;
  return <Circle className="size-5 text-border-strong" aria-hidden />;
}

/** Plan with live step statuses, agent badges and progress; each step expands to its activity. */
export function PlanChecklist({
  plan,
  groups,
  pendingQuestionId = null,
}: {
  plan: PlanView;
  groups: ActivityGroup[];
  pendingQuestionId?: string | null;
}) {
  const { t } = useI18n();
  const progress = planProgress(plan);
  return (
    <div data-testid="plan" className="rounded-xl border border-border bg-surface-2/40">
      <div className="flex items-center gap-3 px-3 pt-3 pb-2 sm:px-3.5">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium tracking-wide text-fg-subtle uppercase">{t("activity.plan")}</p>
          {plan.title && <p className="truncate text-sm font-semibold text-fg">{plan.title}</p>}
        </div>
        <span className="shrink-0 text-xs font-medium text-fg-muted tabular-nums">
          {t("activity.planProgress", { done: progress.done, total: progress.total })}
        </span>
      </div>
      <div className="mx-3 mb-1 h-1 overflow-hidden rounded-full bg-surface-3 sm:mx-3.5" aria-hidden>
        <div
          className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-[width] duration-500"
          style={{ width: `${progress.total ? (progress.done / progress.total) * 100 : 0}%` }}
        />
      </div>
      <ol className="flex flex-col py-1.5">
        {plan.steps.map((step) => (
          <PlanStepRow
            key={step.index}
            step={step}
            groups={groups.filter((group) => group.stepIndex === step.index)}
            pendingQuestionId={pendingQuestionId}
          />
        ))}
      </ol>
    </div>
  );
}

function PlanStepRow({
  step,
  groups,
  pendingQuestionId,
}: {
  step: PlanStep;
  groups: ActivityGroup[];
  pendingQuestionId: string | null;
}) {
  const { t } = useI18n();
  const [override, setOverride] = useState<boolean | null>(null);
  const hasDetails = groups.some((group) => group.items.length > 0 || group.summary);
  const open = override ?? step.status === "in_progress";
  const agent = step.agent ?? groups[0]?.agent ?? null;
  const summary = groups.at(-1)?.summary || step.notes;

  return (
    <li className="px-1 sm:px-1.5">
      <button
        type="button"
        disabled={!hasDetails}
        aria-expanded={hasDetails ? open : undefined}
        onClick={() => setOverride(!open)}
        className={cn(
          "flex w-full items-start gap-3 rounded-lg px-2 py-2 text-left",
          hasDetails && "hover:bg-surface-2",
          "disabled:cursor-default",
        )}
      >
        <span className="mt-px shrink-0">
          <PlanStepIcon status={step.status} />
        </span>
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block text-sm leading-snug",
              step.status === "completed"
                ? "text-fg-muted"
                : step.status === "in_progress"
                  ? "font-medium text-fg"
                  : "text-fg-muted",
            )}
          >
            {step.text}
          </span>
          <span className="mt-1.5 flex flex-wrap items-center gap-2">
            {agent && <AgentBadge agentKey={agent} />}
            {step.status === "blocked" && (
              <span className="text-xs font-medium text-warning">{t("activity.blocked")}</span>
            )}
          </span>
        </span>
        {hasDetails && (
          <ChevronRight
            className={cn(
              "mt-0.5 size-4 shrink-0 text-fg-subtle transition-transform duration-150",
              open && "rotate-90",
            )}
            aria-hidden
          />
        )}
      </button>
      {open && hasDetails && (
        <div className="mb-2 ml-[1.1rem] border-l border-border pt-1 pl-3 sm:pl-5">
          {groups.map((group) => (
            <ItemList key={group.id} items={group.items} pendingQuestionId={pendingQuestionId} />
          ))}
          {summary && step.status !== "in_progress" && (
            <div className="mt-2 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-fg-muted">
              <span className="font-medium text-fg">{t("activity.summary")}: </span>
              {summary}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** Activity of one agent run outside a plan (single-agent mode, planner, finalizer). */
function GroupView({ group, pendingQuestionId }: { group: ActivityGroup; pendingQuestionId: string | null }) {
  const { t } = useI18n();
  const running = group.status === "running";
  const stepText =
    group.step !== null
      ? group.maxSteps
        ? t("activity.agentStep", { step: group.step, max: group.maxSteps })
        : t("activity.agentStepNoMax", { step: group.step })
      : null;
  const finishKey = group.finishReason ? (`activity.finish.${group.finishReason}` as TranslationKey) : null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {group.agent || group.agentTitle ? <AgentBadge agentKey={group.agent} title={group.agentTitle} /> : null}
        {stepText && <span className="text-xs text-fg-subtle tabular-nums">{stepText}</span>}
        {running && <Spinner className="size-3.5 text-accent-text" />}
        {!running && finishKey && (
          <span className={cn("text-xs", group.status === "failed" ? "text-danger" : "text-fg-subtle")}>
            {["terminated", "max_steps", "no_action", "error", "cancelled"].includes(group.finishReason ?? "")
              ? t(finishKey)
              : group.finishReason}
          </span>
        )}
      </div>
      {group.items.length > 0 && (
        <div className="ml-2 border-l border-border pl-3 sm:ml-3 sm:pl-4">
          <ItemList items={group.items} pendingQuestionId={pendingQuestionId} />
        </div>
      )}
    </div>
  );
}

function ItemList({ items, pendingQuestionId }: { items: ActivityItem[]; pendingQuestionId: string | null }) {
  const shown = items.filter((item) => !(item.kind === "question" && item.questionId === pendingQuestionId));
  return (
    <ul className="flex flex-col gap-2 py-1">
      {shown.map((item) => (
        <li key={item.id} className="min-w-0 animate-fade-in">
          <ActivityItemView item={item} />
        </li>
      ))}
    </ul>
  );
}

function Notice({
  icon,
  tone,
  children,
}: {
  icon: ReactNode;
  tone: "warning" | "danger" | "muted";
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg px-3 py-2 text-xs leading-relaxed",
        tone === "warning" && "bg-warning-soft text-fg",
        tone === "danger" && "bg-danger-soft text-danger",
        tone === "muted" && "bg-surface-2 text-fg-muted",
      )}
    >
      <span className="mt-px shrink-0 [&_svg]:size-3.5">{icon}</span>
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}

function Thought({ content }: { content: string }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const long = content.length > 280 || content.split("\n").length > 4;
  return (
    <div className="flex items-start gap-2 text-[0.8125rem] leading-relaxed text-fg-muted">
      <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-amber-500" aria-label={t("activity.thinking")} />
      <div className="min-w-0 flex-1">
        <p className={cn("break-words whitespace-pre-wrap", !expanded && long && "line-clamp-3")}>{content}</p>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="mt-0.5 text-xs font-medium text-accent-text hover:underline"
          >
            {expanded ? t("common.showLess") : t("common.showMore")}
          </button>
        )}
      </div>
    </div>
  );
}

function ActivityItemView({ item }: { item: ActivityItem }) {
  const { t } = useI18n();
  switch (item.kind) {
    case "thought":
      return <Thought content={item.content} />;
    case "tool":
      return <ToolCallCard item={item} />;
    case "stuck":
      return (
        <Notice icon={<TriangleAlert className="text-warning" />} tone="warning">
          {t("activity.stuck")}
        </Notice>
      );
    case "log":
      return (
        <Notice
          icon={item.level === "error" ? <CircleAlert /> : <TriangleAlert className="text-warning" />}
          tone={item.level === "error" ? "danger" : item.level === "warning" ? "warning" : "muted"}
        >
          {item.message}
        </Notice>
      );
    case "question":
      return (
        <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs leading-relaxed">
          <p className="flex items-start gap-2 text-fg">
            <MessageCircleQuestionMark className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
            <span className="line-clamp-4 whitespace-pre-line">
              <span className="font-medium">{t("activity.question")}: </span>
              {item.question}
            </span>
          </p>
          <p className="mt-1 pl-5.5 text-fg-muted">
            <span className="font-medium">{t("activity.yourAnswer")}: </span>
            {item.answer ?? t("activity.noAnswerYet")}
          </p>
        </div>
      );
  }
}
