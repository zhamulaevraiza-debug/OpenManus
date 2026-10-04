/**
 * Pure reduction of a run's event stream (SPEC section 1 events + backend run.* events)
 * into the view model rendered by the activity timeline.
 */
import type { PendingQuestion, Run, RunEvent, RunStatus, Usage } from "@/api/types";

export type ToolStatus = "running" | "success" | "error" | "interrupted";
export type GroupStatus = "running" | "completed" | "blocked" | "failed" | "cancelled";
export type PlanStepStatus = "not_started" | "in_progress" | "completed" | "blocked";

export interface ToolCallItem {
  kind: "tool";
  id: string;
  seq: number;
  agent: string | null;
  step: number | null;
  name: string;
  arguments: Record<string, unknown>;
  status: ToolStatus;
  output: string | null;
  imageUrl: string | null;
  startedAt: string;
  finishedAt: string | null;
}

export interface ThoughtItem {
  kind: "thought";
  id: string;
  seq: number;
  agent: string | null;
  step: number | null;
  content: string;
}

export interface StuckItem {
  kind: "stuck";
  id: string;
  seq: number;
  agent: string | null;
  step: number | null;
}

export interface LogItem {
  kind: "log";
  id: string;
  seq: number;
  level: "warning" | "error" | "info";
  message: string;
}

export interface QuestionItem {
  kind: "question";
  id: string;
  seq: number;
  questionId: string;
  question: string;
  answer: string | null;
}

export type ActivityItem = ToolCallItem | ThoughtItem | StuckItem | LogItem | QuestionItem;

export interface ActivityGroup {
  id: string;
  kind: "plan_step" | "agent";
  agent: string | null;
  agentTitle: string | null;
  /** Plan step index for `plan_step` groups. */
  stepIndex: number | null;
  /** Plan step text for `plan_step` groups. */
  text: string | null;
  status: GroupStatus;
  summary: string | null;
  step: number | null;
  maxSteps: number | null;
  finishReason: string | null;
  items: ActivityItem[];
  startedAt: string;
  finishedAt: string | null;
}

export interface PlanStep {
  index: number;
  text: string;
  agent: string | null;
  status: PlanStepStatus;
  notes: string;
}

export interface PlanView {
  planId: string | null;
  title: string;
  steps: PlanStep[];
}

export interface RouteDecision {
  mode: string;
  agent: string | null;
  reason: string;
}

export interface SandboxInfo {
  vncUrl: string | null;
  websiteUrl: string | null;
}

export interface RunView {
  runId: string;
  lastSeq: number;
  status: RunStatus;
  mode: string | null;
  route: RouteDecision | null;
  plan: PlanView | null;
  groups: ActivityGroup[];
  /** Group receiving agent events right now, if any. */
  openGroupId: string | null;
  /** Streamed final answer (answer.delta) or the complete `final` content. */
  answer: string;
  finalReceived: boolean;
  usage: Usage | null;
  pendingQuestion: PendingQuestion | null;
  sandbox: SandboxInfo | null;
  error: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  durationMs: number | null;
}

const TERMINAL: ReadonlySet<RunStatus> = new Set(["completed", "failed", "cancelled"]);
const RUN_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "running",
  "waiting_input",
  "completed",
  "failed",
  "cancelled",
]);

export function isTerminalStatus(status: RunStatus): boolean {
  return TERMINAL.has(status);
}

export function initialRunView(run: Run): RunView {
  return {
    runId: run.id,
    lastSeq: 0,
    status: run.status,
    mode: run.mode,
    route: null,
    plan: null,
    groups: [],
    openGroupId: null,
    answer: "",
    finalReceived: false,
    usage: run.usage,
    pendingQuestion: run.pending_question,
    sandbox: null,
    error: run.error,
    startedAt: run.started_at,
    finishedAt: run.finished_at,
    durationMs: null,
  };
}

// ---------------------------------------------------------------------------------------------
// Field readers: event data is untrusted JSON.

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asPlanStatus(value: unknown): PlanStepStatus {
  return value === "in_progress" || value === "completed" || value === "blocked" ? value : "not_started";
}

function asUsage(value: unknown): Usage | null {
  const data = record(value);
  const input = num(data.input_tokens);
  const completion = num(data.completion_tokens);
  if (input === null && completion === null) return null;
  return { input_tokens: input ?? 0, completion_tokens: completion ?? 0 };
}

/** Detects the image type of a base64 payload from its first bytes. */
export function imageDataUrl(base64: string): string {
  let mime = "image/png";
  if (base64.startsWith("/9j/")) mime = "image/jpeg";
  else if (base64.startsWith("R0lG")) mime = "image/gif";
  else if (base64.startsWith("UklGR")) mime = "image/webp";
  return `data:${mime};base64,${base64}`;
}

function parsePlan(data: Record<string, unknown>): PlanView {
  const steps = Array.isArray(data.steps) ? data.steps : [];
  return {
    planId: str(data.plan_id),
    title: str(data.title) ?? "",
    steps: steps.map((raw, position) => {
      const step = record(raw);
      return {
        index: num(step.index) ?? position,
        text: str(step.text) ?? "",
        agent: str(step.agent),
        status: asPlanStatus(step.status),
        notes: str(step.notes) ?? "",
      };
    }),
  };
}

// ---------------------------------------------------------------------------------------------
// Draft: copy-on-write state for one batch of events, so long replays stay linear.

interface Draft {
  view: RunView;
  ownedGroups: Set<string>;
}

function createDraft(view: RunView): Draft {
  return { view: { ...view, groups: [...view.groups] }, ownedGroups: new Set() };
}

function groupIndex(draft: Draft, id: string): number {
  const { groups } = draft.view;
  for (let i = groups.length - 1; i >= 0; i--) if (groups[i].id === id) return i;
  return -1;
}

/** Returns a mutable copy of the group (cloned at most once per batch). */
function mutableGroup(draft: Draft, id: string): ActivityGroup | null {
  const index = groupIndex(draft, id);
  if (index === -1) return null;
  if (!draft.ownedGroups.has(id)) {
    const original = draft.view.groups[index];
    draft.view.groups[index] = { ...original, items: [...original.items] };
    draft.ownedGroups.add(id);
  }
  return draft.view.groups[index];
}

function openGroup(draft: Draft, group: Omit<ActivityGroup, "items" | "finishedAt" | "summary" | "finishReason">) {
  const created: ActivityGroup = { ...group, items: [], finishedAt: null, summary: null, finishReason: null };
  draft.view.groups.push(created);
  draft.ownedGroups.add(created.id);
  draft.view.openGroupId = created.id;
  return created;
}

/** The group that should receive an event from `agent`, opening an agent group when needed. */
function targetGroup(draft: Draft, agent: string | null, event: RunEvent): ActivityGroup {
  const openId = draft.view.openGroupId;
  if (openId) {
    const current = mutableGroup(draft, openId);
    if (
      current &&
      (current.kind === "plan_step" || agent === null || current.agent === null || current.agent === agent)
    ) {
      if (current.agent === null && agent !== null) current.agent = agent;
      return current;
    }
  }
  return openGroup(draft, {
    id: `agent-${event.seq}`,
    kind: "agent",
    agent,
    agentTitle: null,
    stepIndex: null,
    text: null,
    status: "running",
    step: null,
    maxSteps: null,
    startedAt: event.ts,
  });
}

function findTool(draft: Draft, callId: string): { group: ActivityGroup; index: number } | null {
  const ordered = [...draft.view.groups].reverse();
  const openId = draft.view.openGroupId;
  if (openId) ordered.sort((a, b) => Number(b.id === openId) - Number(a.id === openId));
  for (const candidate of ordered) {
    const index = candidate.items.findIndex((item) => item.kind === "tool" && item.id === callId);
    if (index !== -1) {
      const group = mutableGroup(draft, candidate.id);
      if (group) return { group, index };
    }
  }
  return null;
}

function updatePlanStep(draft: Draft, index: number, patch: Partial<PlanStep>) {
  const plan = draft.view.plan;
  if (!plan) return;
  draft.view.plan = {
    ...plan,
    steps: plan.steps.map((step) => (step.index === index ? { ...step, ...patch } : step)),
  };
}

function groupStatusFromReason(reason: string | null): GroupStatus {
  if (reason === "error") return "failed";
  if (reason === "cancelled") return "cancelled";
  return "completed";
}

function settleOpenWork(draft: Draft, status: RunStatus, ts: string) {
  const final: GroupStatus = status === "completed" ? "completed" : status === "cancelled" ? "cancelled" : "failed";
  for (const original of draft.view.groups) {
    const hasRunningTool = original.items.some((item) => item.kind === "tool" && item.status === "running");
    if (original.status !== "running" && !hasRunningTool) continue;
    const group = mutableGroup(draft, original.id);
    if (!group) continue;
    if (group.status === "running") {
      group.status = final;
      group.finishedAt = group.finishedAt ?? ts;
    }
    group.items = group.items.map((item) =>
      item.kind === "tool" && item.status === "running" ? { ...item, status: "interrupted", finishedAt: ts } : item,
    );
  }
  draft.view.openGroupId = null;
}

function applyEvent(draft: Draft, event: RunEvent) {
  const view = draft.view;
  const data = record(event.data);
  const agent = str(data.agent);
  view.lastSeq = event.seq;

  switch (event.type) {
    case "run.started": {
      view.status = "running";
      view.mode = str(data.mode) ?? view.mode;
      view.startedAt = view.startedAt ?? event.ts;
      break;
    }
    case "run.status": {
      const status = str(data.status);
      if (status && RUN_STATUSES.has(status)) view.status = status as RunStatus;
      if (view.status === "running" && !view.startedAt) view.startedAt = event.ts;
      break;
    }
    case "run.finished": {
      const status = str(data.status);
      view.status = status && RUN_STATUSES.has(status) ? (status as RunStatus) : "completed";
      view.error = str(data.error) ?? (view.status === "completed" ? null : view.error);
      view.durationMs = num(data.duration_ms);
      view.usage = asUsage(data.usage) ?? view.usage;
      view.finishedAt = event.ts;
      view.pendingQuestion = null;
      settleOpenWork(draft, view.status, event.ts);
      break;
    }
    case "router.decision": {
      view.route = { mode: str(data.mode) ?? "agent", agent, reason: str(data.reason) ?? "" };
      break;
    }
    case "agent.started": {
      const title = str(data.title);
      const maxSteps = num(data.max_steps);
      const openId = view.openGroupId;
      const open = openId ? mutableGroup(draft, openId) : null;
      if (open && open.kind === "plan_step") {
        open.agent = agent ?? open.agent;
        open.agentTitle = title ?? open.agentTitle;
        open.maxSteps = maxSteps ?? open.maxSteps;
      } else {
        openGroup(draft, {
          id: `agent-${event.seq}`,
          kind: "agent",
          agent,
          agentTitle: title,
          stepIndex: null,
          text: null,
          status: "running",
          step: null,
          maxSteps,
          startedAt: event.ts,
        });
      }
      break;
    }
    case "agent.step": {
      const group = targetGroup(draft, agent, event);
      group.step = num(data.step) ?? group.step;
      group.maxSteps = num(data.max_steps) ?? group.maxSteps;
      break;
    }
    case "agent.thought": {
      const content = str(data.content);
      if (!content) break;
      targetGroup(draft, agent, event).items.push({
        kind: "thought",
        id: `thought-${event.seq}`,
        seq: event.seq,
        agent,
        step: num(data.step),
        content,
      });
      break;
    }
    case "tool.call": {
      targetGroup(draft, agent, event).items.push({
        kind: "tool",
        id: str(data.call_id) ?? `call-${event.seq}`,
        seq: event.seq,
        agent,
        step: num(data.step),
        name: str(data.name) ?? "tool",
        arguments: record(data.arguments),
        status: "running",
        output: null,
        imageUrl: null,
        startedAt: event.ts,
        finishedAt: null,
      });
      break;
    }
    case "tool.result": {
      const callId = str(data.call_id);
      const imageB64 = str(data.image_b64);
      const result = {
        status: (data.error === true ? "error" : "success") as ToolStatus,
        output: str(data.output),
        imageUrl: str(data.image_url) ?? (imageB64 ? imageDataUrl(imageB64) : null),
        finishedAt: event.ts,
      };
      const found = callId ? findTool(draft, callId) : null;
      if (found) {
        const item = found.group.items[found.index] as ToolCallItem;
        found.group.items[found.index] = { ...item, ...result };
      } else {
        targetGroup(draft, agent, event).items.push({
          kind: "tool",
          id: callId ?? `call-${event.seq}`,
          seq: event.seq,
          agent,
          step: num(data.step),
          name: str(data.name) ?? "tool",
          arguments: {},
          startedAt: event.ts,
          ...result,
        });
      }
      break;
    }
    case "agent.stuck": {
      targetGroup(draft, agent, event).items.push({
        kind: "stuck",
        id: `stuck-${event.seq}`,
        seq: event.seq,
        agent,
        step: num(data.step),
      });
      break;
    }
    case "agent.finished": {
      const reason = str(data.reason);
      const group = targetGroup(draft, agent, event);
      group.finishReason = reason;
      group.step = num(data.steps) ?? group.step;
      if (group.kind === "agent") {
        group.status = groupStatusFromReason(reason);
        group.finishedAt = event.ts;
        view.openGroupId = null;
      }
      break;
    }
    case "plan.created":
    case "plan.updated": {
      view.plan = parsePlan(data);
      break;
    }
    case "plan.step_started": {
      const index = num(data.index) ?? 0;
      if (view.openGroupId) {
        const previous = mutableGroup(draft, view.openGroupId);
        if (previous && previous.status === "running") {
          previous.status = "completed";
          previous.finishedAt = event.ts;
        }
      }
      const planStep = view.plan?.steps.find((step) => step.index === index);
      openGroup(draft, {
        id: `step-${index}-${event.seq}`,
        kind: "plan_step",
        agent: agent ?? planStep?.agent ?? null,
        agentTitle: null,
        stepIndex: index,
        text: str(data.text) ?? planStep?.text ?? null,
        status: "running",
        step: null,
        maxSteps: null,
        startedAt: event.ts,
      });
      updatePlanStep(draft, index, { status: "in_progress" });
      break;
    }
    case "plan.step_finished": {
      const index = num(data.index) ?? 0;
      const planStatus = asPlanStatus(data.status);
      const summary = str(data.summary);
      const target = [...view.groups]
        .reverse()
        .find((group) => group.kind === "plan_step" && group.stepIndex === index);
      if (target) {
        const group = mutableGroup(draft, target.id);
        if (group) {
          group.status = planStatus === "blocked" ? "blocked" : "completed";
          group.summary = summary ?? group.summary;
          group.finishedAt = event.ts;
        }
        if (view.openGroupId === target.id) view.openGroupId = null;
      }
      updatePlanStep(draft, index, {
        status: planStatus === "not_started" ? "completed" : planStatus,
        ...(summary ? { notes: summary } : {}),
      });
      break;
    }
    case "human.question": {
      const questionId = str(data.question_id) ?? `q-${event.seq}`;
      const question = str(data.question) ?? "";
      view.pendingQuestion = { question_id: questionId, question };
      targetGroup(draft, agent, event).items.push({
        kind: "question",
        id: `question-${event.seq}`,
        seq: event.seq,
        questionId,
        question,
        answer: null,
      });
      break;
    }
    case "human.answer": {
      const questionId = str(data.question_id);
      const answer = str(data.answer) ?? "";
      if (view.pendingQuestion && (!questionId || view.pendingQuestion.question_id === questionId)) {
        view.pendingQuestion = null;
      }
      for (const original of [...view.groups].reverse()) {
        const index = original.items.findIndex((item) => item.kind === "question" && item.questionId === questionId);
        if (index === -1) continue;
        const group = mutableGroup(draft, original.id);
        if (group) group.items[index] = { ...(group.items[index] as QuestionItem), answer };
        break;
      }
      break;
    }
    case "sandbox.ready": {
      view.sandbox = { vncUrl: str(data.vnc_url), websiteUrl: str(data.website_url) };
      break;
    }
    case "answer.delta": {
      if (!view.finalReceived) view.answer += str(data.content) ?? "";
      break;
    }
    case "final": {
      view.answer = str(data.content) ?? view.answer;
      view.finalReceived = true;
      break;
    }
    case "usage": {
      view.usage = asUsage(data) ?? view.usage;
      break;
    }
    case "log": {
      const level = str(data.level);
      targetGroup(draft, agent, event).items.push({
        kind: "log",
        id: `log-${event.seq}`,
        seq: event.seq,
        level: level === "error" || level === "warning" ? level : "info",
        message: str(data.message) ?? "",
      });
      break;
    }
    default:
      // workspace.changed and unknown event types carry no timeline state.
      break;
  }
}

/** Applies events in order, ignoring any already reduced (seq <= lastSeq). Returns `view` if nothing changed. */
export function reduceRunEvents(view: RunView, events: readonly RunEvent[]): RunView {
  let draft: Draft | null = null;
  for (const event of events) {
    const current = draft ? draft.view : view;
    if (event.seq <= current.lastSeq) continue;
    draft ??= createDraft(view);
    applyEvent(draft, event);
  }
  return draft ? draft.view : view;
}

// ---------------------------------------------------------------------------------------------
// Selectors

export function planProgress(plan: PlanView | null): { done: number; total: number } {
  if (!plan) return { done: 0, total: 0 };
  const done = plan.steps.filter((step) => step.status === "completed" || step.status === "blocked").length;
  return { done, total: plan.steps.length };
}

export function countTools(view: RunView): number {
  return view.groups.reduce((total, group) => total + group.items.filter((item) => item.kind === "tool").length, 0);
}

/** Agents that took part, in order of first appearance. */
export function participatingAgents(view: RunView): string[] {
  const seen: string[] = [];
  const add = (agent: string | null) => {
    if (agent && !seen.includes(agent)) seen.push(agent);
  };
  add(view.route?.agent ?? null);
  for (const group of view.groups) add(group.agent);
  return seen;
}
