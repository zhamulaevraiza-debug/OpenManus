import { describe, expect, it, vi } from "vitest";

import { SSEParser, subscribeRunEvents, type StreamState } from "./sse";
import type { RunEvent } from "./types";

describe("SSEParser", () => {
  it("parses complete events with id, event and data", () => {
    const parser = new SSEParser();
    const events = parser.feed('id: 1\nevent: tool.call\ndata: {"a":1}\n\n');
    expect(events).toEqual([{ id: "1", event: "tool.call", data: '{"a":1}' }]);
  });

  it("buffers events split across chunks at arbitrary positions", () => {
    const parser = new SSEParser();
    const text = 'id: 7\nevent: final\ndata: {"content":"héllo"}\n\n';
    const received = [];
    for (const char of text) received.push(...parser.feed(char));
    expect(received).toEqual([{ id: "7", event: "final", data: '{"content":"héllo"}' }]);
  });

  it("handles CRLF and lone CR line endings, including CRLF split across chunks", () => {
    const parser = new SSEParser();
    const first = parser.feed("data: a\r");
    const second = parser.feed("\ndata: b\r\r\n");
    const third = parser.feed("data: c\r\r");
    expect(first).toEqual([]);
    expect(second).toEqual([{ id: null, event: "message", data: "a\nb" }]);
    expect(third).toEqual([{ id: null, event: "message", data: "c" }]);
  });

  it("joins multi-line data, ignores comments, unknown fields and empty events", () => {
    const parser = new SSEParser();
    const events = parser.feed(": ping\n\nretry: 100\nfoo: bar\ndata: line1\ndata:line2\ndata\n\n");
    expect(events).toEqual([{ id: null, event: "message", data: "line1\nline2\n" }]);
  });

  it("keeps the last event id across events", () => {
    const parser = new SSEParser();
    const events = parser.feed("id: 5\ndata: x\n\ndata: y\n\n");
    expect(events.map((event) => event.id)).toEqual(["5", "5"]);
  });
});

// ---------------------------------------------------------------------------------------------

function event(seq: number, type: string, data: Record<string, unknown> = {}): RunEvent {
  return { seq, run_id: "r1", type, ts: `2026-01-01T00:00:${String(seq).padStart(2, "0")}Z`, data };
}

function frame(ev: RunEvent): string {
  return `id: ${ev.seq}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`;
}

/** A streaming Response that emits `chunks` and then closes (or stays open until aborted). */
function streamResponse(chunks: string[], signal: AbortSignal | undefined, keepOpen = false): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (!keepOpen) controller.close();
      signal?.addEventListener("abort", () => {
        try {
          controller.error(new DOMException("Aborted", "AbortError"));
        } catch {
          // already closed
        }
      });
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

function fakeFetch(handlers: Handler[]) {
  const calls: string[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(url);
    const handler = handlers[Math.min(calls.length - 1, handlers.length - 1)];
    return handler(url, init);
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

const fast = { baseBackoffMs: 1, maxBackoffMs: 5, pollIntervalMs: 1, hiddenPollIntervalMs: 1 };

describe("subscribeRunEvents", () => {
  it("delivers events in order and stops after run.finished", async () => {
    const events = [
      event(1, "run.started"),
      event(2, "answer.delta", { content: "Hi" }),
      event(3, "run.finished", { status: "completed" }),
    ];
    const { impl, calls } = fakeFetch([
      (_url, init) => streamResponse([events.map(frame).join("")], init?.signal ?? undefined, true),
    ]);
    const received: RunEvent[] = [];
    const states: StreamState[] = [];
    subscribeRunEvents({
      runId: "r1",
      after: 0,
      fetchImpl: impl,
      onEvents: (batch) => received.push(...batch),
      onStateChange: (state) => states.push(state),
      ...fast,
    });
    await vi.waitFor(() => expect(states.at(-1)).toBe("closed"));
    expect(received.map((ev) => ev.seq)).toEqual([1, 2, 3]);
    expect(calls).toEqual(["/api/runs/r1/events?after=0"]);
    expect(states).toEqual(["connecting", "open", "closed"]);
  });

  it("reconnects after a dropped stream and resumes after the last seq without duplicates", async () => {
    const { impl, calls } = fakeFetch([
      (_url, init) =>
        streamResponse([frame(event(1, "run.started")), frame(event(2, "agent.step"))], init?.signal ?? undefined),
      (_url, init) =>
        streamResponse(
          // The server may replay an already-seen event; it must be ignored.
          [frame(event(2, "agent.step")), frame(event(3, "run.finished", { status: "completed" }))],
          init?.signal ?? undefined,
        ),
    ]);
    const received: number[] = [];
    let closed = false;
    subscribeRunEvents({
      runId: "r1",
      after: 0,
      fetchImpl: impl,
      onEvents: (batch) => received.push(...batch.map((ev) => ev.seq)),
      onStateChange: (state) => {
        if (state === "closed") closed = true;
      },
      ...fast,
    });
    await vi.waitFor(() => expect(closed).toBe(true));
    expect(received).toEqual([1, 2, 3]);
    expect(calls).toEqual(["/api/runs/r1/events?after=0", "/api/runs/r1/events?after=2"]);
  });

  it("falls back to polling events.json after two failed SSE attempts", async () => {
    const pollEvents = [event(1, "run.started"), event(2, "run.finished", { status: "failed" })];
    const { impl, calls } = fakeFetch([
      () => new Response("bad gateway", { status: 502 }),
      () => new Response("bad gateway", { status: 502 }),
      () => new Response(JSON.stringify(pollEvents), { status: 200, headers: { "Content-Type": "application/json" } }),
    ]);
    const received: number[] = [];
    const states: StreamState[] = [];
    subscribeRunEvents({
      runId: "r1",
      after: 0,
      fetchImpl: impl,
      onEvents: (batch) => received.push(...batch.map((ev) => ev.seq)),
      onStateChange: (state) => states.push(state),
      ...fast,
    });
    await vi.waitFor(() => expect(states.at(-1)).toBe("closed"));
    expect(calls).toEqual([
      "/api/runs/r1/events?after=0",
      "/api/runs/r1/events?after=0",
      "/api/runs/r1/events.json?after=0",
    ]);
    expect(received).toEqual([1, 2]);
    expect(states).toContain("polling");
  });

  it("keeps polling from the last seq until the run finishes", async () => {
    const { impl, calls } = fakeFetch([
      () => new Response("", { status: 503 }),
      () => new Response("", { status: 503 }),
      () => new Response(JSON.stringify([event(1, "run.started"), event(2, "agent.step")]), { status: 200 }),
      () => new Response(JSON.stringify([event(3, "run.finished", { status: "completed" })]), { status: 200 }),
    ]);
    let closed = false;
    subscribeRunEvents({
      runId: "r1",
      after: 0,
      fetchImpl: impl,
      onEvents: () => undefined,
      onStateChange: (state) => {
        if (state === "closed") closed = true;
      },
      ...fast,
    });
    await vi.waitFor(() => expect(closed).toBe(true));
    expect(calls.slice(2)).toEqual(["/api/runs/r1/events.json?after=0", "/api/runs/r1/events.json?after=2"]);
  });

  it("stops for good on 404 and reports it", async () => {
    const { impl, calls } = fakeFetch([() => new Response("", { status: 404 })]);
    const onFatal = vi.fn();
    subscribeRunEvents({ runId: "r1", after: 5, fetchImpl: impl, onEvents: () => undefined, onFatal, ...fast });
    await vi.waitFor(() => expect(onFatal).toHaveBeenCalledTimes(1));
    expect(onFatal.mock.calls[0][0].status).toBe(404);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls).toHaveLength(1);
  });

  it("close() aborts the open connection and stops reconnecting", async () => {
    let aborted = false;
    const { impl, calls } = fakeFetch([
      (_url, init) => {
        init?.signal?.addEventListener("abort", () => {
          aborted = true;
        });
        return streamResponse([": ping\n\n"], init?.signal ?? undefined, true);
      },
    ]);
    const states: StreamState[] = [];
    const subscription = subscribeRunEvents({
      runId: "r1",
      after: 0,
      fetchImpl: impl,
      onEvents: () => undefined,
      onStateChange: (state) => states.push(state),
      ...fast,
    });
    await vi.waitFor(() => expect(states).toContain("open"));
    subscription.close();
    await vi.waitFor(() => expect(aborted).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls).toHaveLength(1);
  });

  it("resubscribes immediately when the page becomes visible again", async () => {
    const { impl, calls } = fakeFetch([
      (_url, init) => streamResponse([frame(event(1, "run.started"))], init?.signal ?? undefined, true),
      (_url, init) =>
        streamResponse([frame(event(2, "run.finished", { status: "completed" }))], init?.signal ?? undefined, true),
    ]);
    const received: number[] = [];
    subscribeRunEvents({
      runId: "r1",
      after: 0,
      fetchImpl: impl,
      onEvents: (batch) => received.push(...batch.map((ev) => ev.seq)),
      ...fast,
    });
    await vi.waitFor(() => expect(received).toEqual([1]));
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(received).toEqual([1, 2]));
    expect(calls).toEqual(["/api/runs/r1/events?after=0", "/api/runs/r1/events?after=1"]);
  });
});
