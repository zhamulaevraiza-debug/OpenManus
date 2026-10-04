import { describe, expect, it } from "vitest";

import type { Run, RunEvent } from "@/api/types";

import {
  countTools,
  imageDataUrl,
  initialRunView,
  participatingAgents,
  planProgress,
  reduceRunEvents,
  type ToolCallItem,
} from "./reducer";

const run: Run = {
  id: "r1",
  conversation_id: "c1",
  mode: "auto",
  status: "queued",
  created_at: "2026-01-01T00:00:00Z",
  started_at: null,
  finished_at: null,
  error: null,
  usage: null,
  pending_question: null,
  last_seq: 0,
};

let seq = 0;
function ev(type: string, data: Record<string, unknown> = {}): RunEvent {
  seq += 1;
  return { seq, run_id: "r1", type, ts: `2026-01-01T00:00:${String(seq).padStart(2, "0")}Z`, data };
}

function reduce(events: RunEvent[]) {
  return reduceRunEvents(initialRunView(run), events);
}

describe("reduceRunEvents — single agent", () => {
  seq = 0;
  const events = [
    ev("run.started", { mode: "auto" }),
    ev("run.status", { status: "running" }),
    ev("router.decision", { mode: "agent", agent: "coder", reason: "Needs code" }),
    ev("agent.started", { agent: "coder", title: "Coder", max_steps: 20 }),
    ev("agent.step", { agent: "coder", step: 1, max_steps: 20 }),
    ev("agent.thought", { agent: "coder", step: 1, content: "Let me write a script." }),
    ev("tool.call", {
      agent: "coder",
      step: 1,
      call_id: "c-1",
      name: "python_execute",
      arguments: { code: "print(1)" },
    }),
    ev("tool.result", { agent: "coder", step: 1, call_id: "c-1", name: "python_execute", output: "1", error: false }),
    ev("agent.step", { agent: "coder", step: 2, max_steps: 20 }),
    ev("tool.call", {
      agent: "coder",
      step: 2,
      call_id: "c-2",
      name: "browser_use",
      arguments: { action: "go_to_url" },
    }),
    ev("tool.result", {
      agent: "coder",
      step: 2,
      call_id: "c-2",
      name: "browser_use",
      output: "boom",
      error: true,
      image_b64: "/9j/AAAA",
    }),
    ev("agent.stuck", { agent: "coder", step: 2 }),
    ev("agent.finished", { agent: "coder", steps: 2, reason: "terminated" }),
    ev("answer.delta", { content: "Hel" }),
    ev("answer.delta", { content: "lo" }),
    ev("usage", { input_tokens: 120, completion_tokens: 30 }),
    ev("workspace.changed", { paths: ["a.py"] }),
    ev("run.finished", { status: "completed", duration_ms: 4200, usage: { input_tokens: 150, completion_tokens: 40 } }),
  ];
  const view = reduce(events);

  it("tracks status, route, timing and usage", () => {
    expect(view.status).toBe("completed");
    expect(view.route).toEqual({ mode: "agent", agent: "coder", reason: "Needs code" });
    expect(view.startedAt).toBe(events[0].ts);
    expect(view.finishedAt).toBe(events.at(-1)?.ts);
    expect(view.durationMs).toBe(4200);
    expect(view.usage).toEqual({ input_tokens: 150, completion_tokens: 40 });
    expect(view.lastSeq).toBe(events.length);
  });

  it("groups the agent's activity and matches tool results to calls", () => {
    expect(view.groups).toHaveLength(1);
    const group = view.groups[0];
    expect(group).toMatchObject({ kind: "agent", agent: "coder", agentTitle: "Coder", step: 2, maxSteps: 20 });
    expect(group.status).toBe("completed");
    expect(group.finishReason).toBe("terminated");
    expect(group.items.map((item) => item.kind)).toEqual(["thought", "tool", "tool", "stuck"]);
    const [, first, second] = group.items as ToolCallItem[];
    expect(first).toMatchObject({ id: "c-1", status: "success", output: "1", arguments: { code: "print(1)" } });
    expect(second).toMatchObject({
      id: "c-2",
      status: "error",
      output: "boom",
      imageUrl: "data:image/jpeg;base64,/9j/AAAA",
    });
    expect(countTools(view)).toBe(2);
  });

  it("accumulates streamed answer deltas", () => {
    expect(view.answer).toBe("Hello");
    expect(view.finalReceived).toBe(false);
  });

  it("closes the open group after agent.finished", () => {
    expect(view.openGroupId).toBeNull();
  });
});

describe("reduceRunEvents — team plan", () => {
  seq = 0;
  const steps = [
    { index: 0, text: "Research prices", agent: "researcher", status: "not_started", notes: "" },
    { index: 1, text: "Write report", agent: "writer", status: "not_started", notes: "" },
  ];
  const events = [
    ev("run.started", { mode: "team" }),
    ev("router.decision", { mode: "team", agent: null, reason: "Multi-skill" }),
    ev("plan.created", { plan_id: "p1", title: "E-bike report", steps }),
    ev("plan.step_started", { index: 0, agent: "researcher", text: "Research prices" }),
    ev("agent.started", { agent: "researcher", title: "Researcher", max_steps: 10 }),
    ev("tool.call", {
      agent: "researcher",
      step: 1,
      call_id: "x",
      name: "web_search",
      arguments: { query: "e-bike prices" },
    }),
    ev("tool.result", {
      agent: "researcher",
      step: 1,
      call_id: "x",
      name: "web_search",
      output: "results",
      error: false,
    }),
    ev("agent.finished", { agent: "researcher", steps: 1, reason: "terminated" }),
    ev("plan.step_finished", { index: 0, agent: "researcher", status: "completed", summary: "Prices collected" }),
    ev("plan.step_started", { index: 1, agent: "writer", text: "Write report" }),
    ev("agent.thought", { agent: "writer", step: 1, content: "Drafting" }),
  ];
  const view = reduce(events);

  it("keeps the plan snapshot with live step statuses and notes", () => {
    expect(view.plan?.title).toBe("E-bike report");
    expect(view.plan?.steps.map((step) => step.status)).toEqual(["completed", "in_progress"]);
    expect(view.plan?.steps[0].notes).toBe("Prices collected");
    expect(planProgress(view.plan)).toEqual({ done: 1, total: 2 });
  });

  it("creates one group per plan step with its agent and summary", () => {
    const stepGroups = view.groups.filter((group) => group.kind === "plan_step");
    expect(stepGroups).toHaveLength(2);
    expect(stepGroups[0]).toMatchObject({
      stepIndex: 0,
      agent: "researcher",
      agentTitle: "Researcher",
      status: "completed",
      summary: "Prices collected",
    });
    expect(stepGroups[0].items.map((item) => item.kind)).toEqual(["tool"]);
    expect(stepGroups[1]).toMatchObject({ stepIndex: 1, agent: "writer", status: "running" });
    expect(stepGroups[1].items.map((item) => item.kind)).toEqual(["thought"]);
    expect(view.openGroupId).toBe(stepGroups[1].id);
  });

  it("applies later plan.updated snapshots", () => {
    const updated = reduceRunEvents(view, [
      ev("plan.updated", {
        plan_id: "p1",
        title: "E-bike report",
        steps: steps.map((step) => ({ ...step, status: "completed" })),
      }),
    ]);
    expect(planProgress(updated.plan)).toEqual({ done: 2, total: 2 });
  });

  it("lists participating agents in order", () => {
    expect(participatingAgents(view)).toEqual(["researcher", "writer"]);
  });

  it("settles running work when the run is cancelled", () => {
    const cancelled = reduceRunEvents(view, [
      ev("tool.call", { agent: "writer", step: 1, call_id: "w", name: "str_replace_editor", arguments: {} }),
      ev("run.finished", { status: "cancelled" }),
    ]);
    const writer = cancelled.groups.find((group) => group.stepIndex === 1);
    expect(writer?.status).toBe("cancelled");
    expect((writer?.items.at(-1) as ToolCallItem).status).toBe("interrupted");
    expect(cancelled.status).toBe("cancelled");
  });
});

describe("reduceRunEvents — human input and misc", () => {
  it("tracks pending questions and answers", () => {
    seq = 0;
    const asked = reduce([
      ev("run.started", {}),
      ev("agent.started", { agent: "manus", title: "Manus", max_steps: 20 }),
      ev("human.question", { question_id: "q1", question: "Which city?" }),
      ev("run.status", { status: "waiting_input" }),
    ]);
    expect(asked.status).toBe("waiting_input");
    expect(asked.pendingQuestion).toEqual({ question_id: "q1", question: "Which city?" });
    const answered = reduceRunEvents(asked, [
      ev("human.answer", { question_id: "q1", answer: "Paris" }),
      ev("run.status", { status: "running" }),
    ]);
    expect(answered.pendingQuestion).toBeNull();
    const question = answered.groups[0].items.find((item) => item.kind === "question");
    expect(question).toMatchObject({ question: "Which city?", answer: "Paris" });
  });

  it("final replaces streamed content and ignores later deltas", () => {
    seq = 0;
    const view = reduce([
      ev("answer.delta", { content: "draft" }),
      ev("final", { content: "**Final** answer" }),
      ev("answer.delta", { content: " extra" }),
    ]);
    expect(view.answer).toBe("**Final** answer");
    expect(view.finalReceived).toBe(true);
  });

  it("ignores duplicates and returns the same object when nothing changes", () => {
    seq = 0;
    const events = [ev("run.started", {}), ev("log", { level: "warning", message: "slow" })];
    const view = reduce(events);
    expect(reduceRunEvents(view, events)).toBe(view);
    expect(view.groups[0].items[0]).toMatchObject({ kind: "log", level: "warning", message: "slow" });
  });

  it("does not mutate the previous view", () => {
    seq = 0;
    const before = reduce([
      ev("agent.started", { agent: "manus" }),
      ev("tool.call", { agent: "manus", call_id: "t", name: "bash", arguments: {} }),
    ]);
    const snapshot = JSON.stringify(before);
    reduceRunEvents(before, [ev("tool.result", { call_id: "t", output: "ok", error: false })]);
    expect(JSON.stringify(before)).toBe(snapshot);
  });

  it("records failures, sandbox links, unknown events and a result without a call", () => {
    seq = 0;
    const view = reduce([
      ev("sandbox.ready", { vnc_url: "https://vnc", website_url: null }),
      ev("something.new", { x: 1 }),
      ev("tool.result", {
        agent: "browser",
        call_id: "orphan",
        name: "browser_use",
        output: "done",
        error: false,
        image_url: "/api/runs/r1/artifacts/a.jpg",
      }),
      ev("run.finished", { status: "failed", error: "timeout" }),
    ]);
    expect(view.sandbox).toEqual({ vncUrl: "https://vnc", websiteUrl: null });
    expect(view.status).toBe("failed");
    expect(view.error).toBe("timeout");
    expect(view.groups[0].items[0]).toMatchObject({
      kind: "tool",
      id: "orphan",
      imageUrl: "/api/runs/r1/artifacts/a.jpg",
    });
    expect(view.lastSeq).toBe(4);
  });

  it("opens a new group when another agent starts reporting", () => {
    seq = 0;
    const view = reduce([
      ev("agent.thought", { agent: "manus", content: "a" }),
      ev("agent.thought", { agent: "browser", content: "b" }),
    ]);
    expect(view.groups.map((group) => group.agent)).toEqual(["manus", "browser"]);
  });
});

describe("imageDataUrl", () => {
  it("detects common image types from base64 magic bytes", () => {
    expect(imageDataUrl("iVBORw0KGgo")).toBe("data:image/png;base64,iVBORw0KGgo");
    expect(imageDataUrl("/9j/4AAQ")).toMatch(/^data:image\/jpeg/);
    expect(imageDataUrl("R0lGODlh")).toMatch(/^data:image\/gif/);
    expect(imageDataUrl("UklGRiQAAABXRUJQ")).toMatch(/^data:image\/webp/);
  });
});
