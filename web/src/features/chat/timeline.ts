/**
 * Orders a conversation's messages and runs into the rendered chat timeline.
 */
import type { Message, Run } from "@/api/types";

export type TimelineEntry = { kind: "message"; message: Message } | { kind: "run"; run: Run; message: Message | null };

interface Keyed {
  entry: TimelineEntry;
  time: number;
  /** Tie-breaker at equal times: user messages first, then runs and replies. */
  rank: number;
  order: number;
}

function toTime(iso: string | null | undefined): number {
  const value = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(value) ? 0 : value;
}

/**
 * Every run becomes one entry that carries its assistant reply (if any) and is placed at the reply's
 * time. A run without a reply (active, or ended without one) is placed at its creation time, never
 * before the user message that started it.
 */
export function buildTimeline(messages: readonly Message[], runs: readonly Run[]): TimelineEntry[] {
  const runIds = new Set(runs.map((run) => run.id));
  const replyByRun = new Map<string, Message>();
  for (const message of messages) {
    if (message.role === "assistant" && message.run_id && runIds.has(message.run_id)) {
      replyByRun.set(message.run_id, message);
    }
  }

  const keyed: Keyed[] = [];
  messages.forEach((message, order) => {
    if (message.run_id && replyByRun.get(message.run_id) === message) return;
    keyed.push({
      entry: { kind: "message", message },
      time: toTime(message.created_at),
      rank: message.role === "user" ? 0 : 1,
      order,
    });
  });
  runs.forEach((run, order) => {
    const reply = replyByRun.get(run.id) ?? null;
    let time = toTime(reply ? reply.created_at : run.created_at);
    if (!reply) {
      const trigger = messages.find((message) => message.role === "user" && message.run_id === run.id);
      if (trigger) time = Math.max(time, toTime(trigger.created_at));
    }
    keyed.push({ entry: { kind: "run", run, message: reply }, time, rank: 1, order: messages.length + order });
  });

  keyed.sort((a, b) => a.time - b.time || a.rank - b.rank || a.order - b.order);
  return keyed.map((item) => item.entry);
}
