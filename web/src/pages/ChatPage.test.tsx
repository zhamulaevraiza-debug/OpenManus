import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { ConversationDetail, Run, RunEvent } from "@/api/types";
import { ShellContext } from "@/features/shell/shell";
import { jsonResponse, mockFetch, renderWithProviders } from "@/test/render";

import { ChatRoute } from "./ChatPage";

const conversation = {
  id: "c1",
  title: "Greeting",
  mode: "auto",
  pinned: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  last_message_preview: null,
  active_run_id: "r1",
};

const userMessage = {
  id: "m1",
  conversation_id: "c1",
  role: "user" as const,
  content: "Say hello",
  run_id: "r1",
  attachments: [],
  created_at: "2026-01-01T00:00:01Z",
};

const run: Run = {
  id: "r1",
  conversation_id: "c1",
  mode: "auto",
  status: "running",
  created_at: "2026-01-01T00:00:01Z",
  started_at: "2026-01-01T00:00:01Z",
  finished_at: null,
  error: null,
  usage: null,
  pending_question: null,
  last_seq: 0,
};

const events: RunEvent[] = [
  { type: "run.started", data: { mode: "auto" } },
  { type: "router.decision", data: { mode: "agent", agent: "coder", reason: "Code needed" } },
  { type: "agent.started", data: { agent: "coder", title: "Coder", max_steps: 5 } },
  {
    type: "tool.call",
    data: { agent: "coder", step: 1, call_id: "t1", name: "bash", arguments: { command: "echo hello" } },
  },
  {
    type: "tool.result",
    data: { agent: "coder", step: 1, call_id: "t1", name: "bash", output: "hello", error: false },
  },
  { type: "agent.finished", data: { agent: "coder", steps: 1, reason: "terminated" } },
  { type: "answer.delta", data: { content: "Hello, " } },
  { type: "answer.delta", data: { content: "world!" } },
  { type: "final", data: { content: "Hello, world!" } },
  {
    type: "run.finished",
    data: { status: "completed", duration_ms: 1500, usage: { input_tokens: 10, completion_tokens: 5 } },
  },
].map((event, index) => ({ seq: index + 1, run_id: "r1", ts: `2026-01-01T00:00:0${index}Z`, ...event }));

function sse(list: RunEvent[]): Response {
  const body = list
    .map((event) => `id: ${event.seq}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`)
    .join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("ChatPage live run", () => {
  it("streams a run into the activity timeline and swaps in the final reply", async () => {
    let detailCalls = 0;
    const finished: ConversationDetail = {
      conversation: { ...conversation, active_run_id: null },
      messages: [
        userMessage,
        { ...userMessage, id: "m2", role: "assistant", content: "Hello, world!", created_at: "2026-01-01T00:00:09Z" },
      ],
      runs: [{ ...run, status: "completed", finished_at: "2026-01-01T00:00:09Z", last_seq: events.length }],
    };
    const calls = mockFetch((url) => {
      if (url === "/api/conversations/c1") {
        detailCalls += 1;
        return jsonResponse(detailCalls === 1 ? { conversation, messages: [userMessage], runs: [run] } : finished);
      }
      if (url.startsWith("/api/runs/r1/events?after=0")) return sse(events);
      if (url === "/api/agents")
        return jsonResponse({ modes: [{ key: "auto" }, { key: "chat" }, { key: "team" }], agents: [] });
      if (url === "/api/auth/me")
        return jsonResponse({ id: "u", username: "alice", is_admin: false, disabled: false, created_at: "" });
      if (url === "/api/status")
        return jsonResponse({
          version: "1",
          llm_configured: true,
          active_runs: 0,
          max_concurrent_runs: 4,
          is_admin: false,
        });
      if (url.startsWith("/api/conversations")) return jsonResponse([]);
      return undefined;
    });

    renderWithProviders(
      <ShellContext.Provider
        value={{
          drawerOpen: false,
          setDrawerOpen: () => undefined,
          sidebarCollapsed: false,
          setSidebarCollapsed: () => undefined,
        }}
      >
        <Routes>
          <Route path="/c/:conversationId" element={<ChatRoute />} />
        </Routes>
      </ShellContext.Provider>,
      { route: "/c/c1" },
    );

    expect(await screen.findByTestId("message-user")).toHaveTextContent("Say hello");
    await waitFor(() => expect(screen.getByTestId("activity")).toHaveAttribute("data-status", "completed"));
    await waitFor(() => expect(screen.getByTestId("message-assistant")).toHaveTextContent("Hello, world!"));
    // The composer switches back from Stop to Send once the run has finished.
    await waitFor(() => expect(screen.getByTestId("composer-send")).toBeInTheDocument());
    expect(screen.queryByTestId("stop-run")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.url.startsWith("/api/runs/r1/events")).length).toBe(1);
    expect(detailCalls).toBeGreaterThanOrEqual(2);
  });
});
