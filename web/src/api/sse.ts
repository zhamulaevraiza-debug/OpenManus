/**
 * Run event streaming: an incremental Server-Sent-Events parser and a resilient run subscription
 * (fetch stream reader, exponential backoff reconnect resuming after the last seq,
 * resubscribe on visibility/online, and a polling fallback after repeated SSE failures).
 */
import { API_BASE, ApiError } from "./client";
import type { RunEvent } from "./types";

export interface SSEMessage {
  id: string | null;
  event: string;
  data: string;
}

/** Incremental parser for the `text/event-stream` format (WHATWG HTML §9.2.6). */
export class SSEParser {
  private buffer = "";
  private dataLines: string[] = [];
  private eventType = "";
  private eventId: string | null = null;
  private pendingCR = false;

  /** Feeds a decoded chunk; returns the events completed by it. */
  feed(chunk: string): SSEMessage[] {
    let text = chunk;
    // A CR at the end of the previous chunk may be the first half of a CRLF pair.
    if (this.pendingCR) {
      this.pendingCR = false;
      if (text.startsWith("\n")) text = text.slice(1);
    }
    this.buffer += text;
    const messages: SSEMessage[] = [];
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const char = this.buffer[i];
      if (char !== "\n" && char !== "\r") continue;
      const line = this.buffer.slice(start, i);
      if (char === "\r") {
        if (i + 1 < this.buffer.length) {
          if (this.buffer[i + 1] === "\n") i++;
        } else {
          this.pendingCR = true;
        }
      }
      start = i + 1;
      const message = this.processLine(line);
      if (message) messages.push(message);
    }
    this.buffer = this.buffer.slice(start);
    return messages;
  }

  private processLine(line: string): SSEMessage | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "data":
        this.dataLines.push(value);
        break;
      case "event":
        this.eventType = value;
        break;
      case "id":
        if (!value.includes("\0")) this.eventId = value;
        break;
      default:
        // `retry` and unknown fields are ignored: reconnection timing is ours.
        break;
    }
    return null;
  }

  private dispatch(): SSEMessage | null {
    const hasData = this.dataLines.length > 0;
    const message: SSEMessage = {
      id: this.eventId,
      event: this.eventType || "message",
      data: this.dataLines.join("\n"),
    };
    this.dataLines = [];
    this.eventType = "";
    return hasData ? message : null;
  }
}

export type StreamState = "connecting" | "open" | "reconnecting" | "polling" | "closed";

export interface RunSubscriptionOptions {
  runId: string;
  /** Resume after this sequence number (0 = from the beginning). */
  after: number;
  /** Receives events in order, de-duplicated by seq, batched per network chunk. */
  onEvents: (events: RunEvent[]) => void;
  onStateChange?: (state: StreamState) => void;
  /** Called once when the subscription stops for good because of an error (401/403/404). */
  onFatal?: (error: ApiError) => void;
  fetchImpl?: typeof fetch;
  /** Consecutive SSE failures before switching to polling `events.json`. */
  sseFailuresBeforePolling?: number;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  pollIntervalMs?: number;
  hiddenPollIntervalMs?: number;
  /** Abort a connection that delivered no bytes (not even a `: ping`) for this long. */
  stallTimeoutMs?: number;
}

export interface RunSubscription {
  /** Stops the subscription and aborts any request in flight. */
  close: () => void;
  /** Forces an immediate reconnect from the last seen seq. */
  resync: () => void;
}

const TERMINAL_EVENT = "run.finished";

type AttemptOutcome = "finished" | "dropped" | "failed" | "interrupted" | "fatal";

function parseRunEvent(message: SSEMessage): RunEvent | null {
  try {
    const value = JSON.parse(message.data) as RunEvent;
    if (typeof value?.seq !== "number" || typeof value.type !== "string") return null;
    return { ...value, data: value.data ?? {} };
  } catch {
    return null;
  }
}

/** Subscribes to a run's events. Stops by itself after `run.finished`. */
export function subscribeRunEvents(options: RunSubscriptionOptions): RunSubscription {
  const {
    runId,
    onEvents,
    onStateChange,
    onFatal,
    fetchImpl = (input, init) => fetch(input, init),
    sseFailuresBeforePolling = 2,
    baseBackoffMs = 1000,
    maxBackoffMs = 15000,
    pollIntervalMs = 2000,
    hiddenPollIntervalMs = 10000,
    stallTimeoutMs = 45000,
  } = options;

  let lastSeq = options.after;
  let closed = false;
  let finished = false;
  let failures = 0;
  let polling = false;
  let controller: AbortController | null = null;
  let wake: (() => void) | null = null;
  let state: StreamState | null = null;

  const setState = (next: StreamState) => {
    if (state === next) return;
    state = next;
    onStateChange?.(next);
  };

  const deliver = (events: RunEvent[]) => {
    const fresh: RunEvent[] = [];
    for (const event of events) {
      if (event.seq <= lastSeq) continue;
      lastSeq = event.seq;
      fresh.push(event);
      if (event.type === TERMINAL_EVENT) {
        finished = true;
        break;
      }
    }
    if (fresh.length > 0) onEvents(fresh);
  };

  /** Interruptible delay: resync/visibility/online wake it early. */
  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(done, ms);
      function done() {
        clearTimeout(timer);
        wake = null;
        resolve();
      }
      wake = done;
    });

  const backoffDelay = () => {
    const exp = Math.min(maxBackoffMs, baseBackoffMs * 2 ** Math.max(0, failures - 1));
    return exp / 2 + Math.random() * (exp / 2);
  };

  const fatal = (error: ApiError): AttemptOutcome => {
    closed = true;
    onFatal?.(error);
    return "fatal";
  };

  async function streamOnce(): Promise<AttemptOutcome> {
    const attempt = new AbortController();
    controller = attempt;
    let received = false;
    let stalled = false;
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
    const armWatchdog = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => {
        stalled = true;
        attempt.abort();
      }, stallTimeoutMs);
    };
    try {
      armWatchdog();
      const response = await fetchImpl(`${API_BASE}/runs/${encodeURIComponent(runId)}/events?after=${lastSeq}`, {
        headers: { Accept: "text/event-stream" },
        credentials: "same-origin",
        cache: "no-store",
        signal: attempt.signal,
      });
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        return fatal(new ApiError(response.status, response.statusText));
      }
      if (!response.ok || !response.body) return "failed";
      setState("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SSEParser();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        received = true;
        armWatchdog();
        const batch: RunEvent[] = [];
        for (const message of parser.feed(decoder.decode(value, { stream: true }))) {
          const event = parseRunEvent(message);
          if (event) batch.push(event);
        }
        deliver(batch);
        if (finished) {
          attempt.abort();
          return "finished";
        }
      }
      return received ? "dropped" : "failed";
    } catch {
      if (closed || (attempt.signal.aborted && !stalled)) return "interrupted";
      return received ? "dropped" : "failed";
    } finally {
      clearTimeout(stallTimer);
      if (controller === attempt) controller = null;
    }
  }

  async function pollOnce(): Promise<AttemptOutcome | "more"> {
    const attempt = new AbortController();
    controller = attempt;
    try {
      const response = await fetchImpl(`${API_BASE}/runs/${encodeURIComponent(runId)}/events.json?after=${lastSeq}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
        signal: attempt.signal,
      });
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        return fatal(new ApiError(response.status, response.statusText));
      }
      if (!response.ok) return "failed";
      const events = ((await response.json()) as RunEvent[]).filter((event) => typeof event?.seq === "number");
      deliver(events.map((event) => ({ ...event, data: event.data ?? {} })));
      if (finished) return "finished";
      return events.length >= 500 ? "more" : "dropped";
    } catch {
      return closed || attempt.signal.aborted ? "interrupted" : "failed";
    } finally {
      if (controller === attempt) controller = null;
    }
  }

  async function loop() {
    setState("connecting");
    while (!closed && !finished) {
      if (!polling) {
        const seqBefore = lastSeq;
        const outcome = await streamOnce();
        if (outcome === "finished" || outcome === "fatal" || closed) break;
        if (outcome === "interrupted") {
          setState("reconnecting");
          continue;
        }
        if (outcome === "dropped" && lastSeq > seqBefore) {
          // The stream delivered events and was then cut (proxy timeout, restart): resume promptly.
          failures = 0;
          setState("reconnecting");
          await sleep(baseBackoffMs / 4);
          continue;
        }
        failures += 1;
        if (failures >= sseFailuresBeforePolling) {
          polling = true;
          failures = 0;
          setState("polling");
          continue;
        }
        setState("reconnecting");
        await sleep(backoffDelay());
      } else {
        const outcome = await pollOnce();
        if (outcome === "finished" || outcome === "fatal" || closed) break;
        if (outcome === "more" || outcome === "interrupted") continue;
        if (outcome === "failed") {
          failures += 1;
          await sleep(backoffDelay());
          continue;
        }
        failures = 0;
        const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
        await sleep(hidden ? hiddenPollIntervalMs : pollIntervalMs);
      }
    }
    teardown();
    setState("closed");
  }

  const resync = () => {
    if (closed || finished) return;
    if (wake) wake();
    else controller?.abort();
  };

  const onVisibility = () => {
    if (document.visibilityState === "visible") resync();
  };

  function teardown() {
    if (typeof window !== "undefined") {
      window.removeEventListener("online", resync);
      document.removeEventListener("visibilitychange", onVisibility);
    }
  }

  if (typeof window !== "undefined") {
    window.addEventListener("online", resync);
    document.addEventListener("visibilitychange", onVisibility);
  }

  void loop();

  return {
    close: () => {
      if (closed) return;
      closed = true;
      controller?.abort();
      wake?.();
      teardown();
    },
    resync,
  };
}
