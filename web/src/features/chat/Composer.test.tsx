import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { queryKeys } from "@/api/queries";
import type { ConversationDetail, Run } from "@/api/types";
import { TOUCH_QUERY } from "@/hooks/useMediaQuery";
import { jsonResponse, mockFetch, mockMediaQueries, renderWithProviders } from "@/test/render";

import { Composer } from "./Composer";
import { takeDraft } from "./drafts";

const detail: ConversationDetail = {
  conversation: {
    id: "c1",
    title: "Chat",
    mode: "auto",
    pinned: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    last_message_preview: null,
    active_run_id: null,
  },
  messages: [],
  runs: [],
};

const run: Run = {
  id: "r1",
  conversation_id: "c1",
  mode: "auto",
  status: "queued",
  created_at: "2026-01-01T00:00:01Z",
  started_at: null,
  finished_at: null,
  error: null,
  usage: null,
  pending_question: null,
  last_seq: 0,
};

function setup({ activeRun = null, onStop = vi.fn() }: { activeRun?: Run | null; onStop?: () => void } = {}) {
  const calls = mockFetch((url, init) => {
    if (url === "/api/conversations/c1/messages" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return jsonResponse({
        message: {
          id: "m1",
          conversation_id: "c1",
          role: "user",
          content: body.content,
          run_id: "r1",
          attachments: [],
          created_at: "2026-01-01T00:00:01Z",
        },
        run,
      });
    }
    if (url.startsWith("/api/conversations")) return jsonResponse([]);
    if (url === "/api/status")
      return jsonResponse({
        version: "1",
        llm_configured: true,
        active_runs: 1,
        max_concurrent_runs: 4,
        is_admin: false,
      });
    return undefined;
  });
  const view = renderWithProviders(
    <Composer
      conversationId="c1"
      mode="coder"
      onModeChange={() => undefined}
      modes={[{ key: "auto" }, { key: "chat" }, { key: "team" }]}
      agents={[]}
      activeRun={activeRun}
      onStop={onStop}
    />,
  );
  view.queryClient.setQueryData(queryKeys.conversation("c1"), detail);
  return { ...view, calls, onStop };
}

describe("Composer", () => {
  // Drafts live in a module-level store; start every test with an empty composer.
  afterEach(() => {
    takeDraft("c1");
  });

  it("sends on Enter with the selected mode on keyboard devices and clears the input", async () => {
    const user = userEvent.setup();
    const { calls, queryClient } = setup();
    const input = screen.getByTestId("composer-input");
    await user.type(input, "Build a CLI{Enter}");
    await waitFor(() => expect(calls.some((call) => call.url === "/api/conversations/c1/messages")).toBe(true));
    const post = calls.find((call) => call.url === "/api/conversations/c1/messages");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ content: "Build a CLI", mode: "coder" });
    expect(input).toHaveValue("");
    await waitFor(() =>
      expect(queryClient.getQueryData<ConversationDetail>(queryKeys.conversation("c1"))?.runs.map((r) => r.id)).toEqual(
        ["r1"],
      ),
    );
    const cached = queryClient.getQueryData<ConversationDetail>(queryKeys.conversation("c1"));
    expect(cached?.messages.map((message) => message.id)).toEqual(["m1"]);
  });

  it("inserts a newline with Shift+Enter instead of sending", async () => {
    const user = userEvent.setup();
    const { calls } = setup();
    const input = screen.getByTestId("composer-input");
    await user.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(input).toHaveValue("line one\nline two");
    expect(calls.some((call) => call.url.endsWith("/messages"))).toBe(false);
  });

  it("on touch devices Enter adds a newline and the send button sends", async () => {
    mockMediaQueries([TOUCH_QUERY]);
    const user = userEvent.setup();
    const { calls } = setup();
    const input = screen.getByTestId("composer-input");
    await user.type(input, "hello{Enter}world");
    expect(input).toHaveValue("hello\nworld");
    expect(screen.getByRole("button", { name: "Take a photo" })).toBeInTheDocument();
    await user.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith("/messages"))).toBe(true));
  });

  it("disables sending while empty and shows Stop during an active run", async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();
    setup({ activeRun: { ...run, status: "running" }, onStop });
    expect(screen.queryByTestId("composer-send")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("stop-run"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("keeps the send button disabled for whitespace-only input", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByTestId("composer-input"), "   ");
    expect(screen.getByTestId("composer-send")).toBeDisabled();
  });
});
