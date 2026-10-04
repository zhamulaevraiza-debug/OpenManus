import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Run, RunEvent } from "@/api/types";
import { renderWithProviders } from "@/test/render";

import { RunActivity } from "./ActivityTimeline";
import { initialRunView, reduceRunEvents } from "./reducer";

const run: Run = {
  id: "r1",
  conversation_id: "c1",
  mode: "team",
  status: "running",
  created_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:00Z",
  finished_at: null,
  error: null,
  usage: null,
  pending_question: null,
  last_seq: 0,
};

let seq = 0;
const ev = (type: string, data: Record<string, unknown>): RunEvent => ({
  seq: ++seq,
  run_id: "r1",
  type,
  ts: `2026-01-01T00:00:0${seq % 10}Z`,
  data,
});

const view = reduceRunEvents(initialRunView(run), [
  ev("router.decision", { mode: "team", agent: null, reason: "Research and writing" }),
  ev("plan.created", {
    plan_id: "p",
    title: "Market report",
    steps: [
      { index: 0, text: "Collect prices", agent: "researcher", status: "not_started", notes: "" },
      { index: 1, text: "Write the report", agent: "writer", status: "not_started", notes: "" },
    ],
  }),
  ev("plan.step_started", { index: 0, agent: "researcher", text: "Collect prices" }),
  ev("tool.call", {
    agent: "researcher",
    step: 1,
    call_id: "t1",
    name: "web_search",
    arguments: { query: "bike prices" },
  }),
  ev("tool.result", {
    agent: "researcher",
    step: 1,
    call_id: "t1",
    name: "web_search",
    output: "Found 10 results",
    error: false,
  }),
  ev("plan.step_finished", { index: 0, agent: "researcher", status: "completed", summary: "Prices collected" }),
  ev("plan.step_started", { index: 1, agent: "writer", text: "Write the report" }),
  ev("tool.call", {
    agent: "writer",
    step: 1,
    call_id: "t2",
    name: "str_replace_editor",
    arguments: { command: "create", path: "report.md" },
  }),
]);

describe("RunActivity", () => {
  it("shows status, plan progress, agent badges and live steps", () => {
    renderWithProviders(<RunActivity run={run} view={view} loading={false} expanded onToggle={() => undefined} />);
    const activity = screen.getByTestId("activity");
    expect(activity).toHaveAttribute("data-status", "running");
    expect(within(activity).getAllByText("1 of 2").length).toBeGreaterThan(0);
    const plan = screen.getByTestId("plan");
    expect(within(plan).getByText("Market report")).toBeInTheDocument();
    expect(within(plan).getByText("Researcher")).toBeInTheDocument();
    expect(within(plan).getByText("Writer")).toBeInTheDocument();
    // The in-progress step is expanded and shows its running tool call.
    const tools = screen.getAllByTestId("tool-call");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toHaveAttribute("data-status", "running");
    expect(within(tools[0]).getByText("create · report.md")).toBeInTheDocument();
  });

  it("expands a finished step and a tool call to reveal arguments and output", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RunActivity run={run} view={view} loading={false} expanded onToggle={() => undefined} />);
    await user.click(screen.getByRole("button", { name: /Collect prices/ }));
    const searchCall = screen.getAllByTestId("tool-call").find((element) => within(element).queryByText("bike prices"));
    expect(searchCall).toBeDefined();
    await user.click(within(searchCall as HTMLElement).getByRole("button", { expanded: false }));
    expect(within(searchCall as HTMLElement).getByText("Found 10 results")).toBeInTheDocument();
    expect(screen.getByText("Prices collected")).toBeInTheDocument();
  });

  it("shows the router's reason only when the mode was chosen automatically", () => {
    const { unmount } = renderWithProviders(
      <RunActivity run={run} view={view} loading={false} expanded onToggle={() => undefined} />,
    );
    expect(screen.queryByText("Research and writing")).not.toBeInTheDocument();
    unmount();
    renderWithProviders(
      <RunActivity run={{ ...run, mode: "auto" }} view={view} loading={false} expanded onToggle={() => undefined} />,
    );
    expect(screen.getByText("Research and writing")).toBeInTheDocument();
  });

  it("collapses to a header and toggles", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    renderWithProviders(
      <RunActivity
        run={{ ...run, status: "completed" }}
        view={view}
        loading={false}
        expanded={false}
        onToggle={onToggle}
      />,
    );
    expect(screen.queryByTestId("plan")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show activity" }));
    expect(onToggle).toHaveBeenCalled();
  });
});
