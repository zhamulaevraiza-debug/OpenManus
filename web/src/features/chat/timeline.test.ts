import { describe, expect, it } from "vitest";

import type { Message, Run } from "@/api/types";

import { buildTimeline } from "./timeline";

function message(id: string, role: Message["role"], createdAt: string, runId: string | null = null): Message {
  return { id, conversation_id: "c", role, content: id, run_id: runId, attachments: [], created_at: createdAt };
}

function run(id: string, createdAt: string, status: Run["status"] = "completed"): Run {
  return {
    id,
    conversation_id: "c",
    mode: "auto",
    status,
    created_at: createdAt,
    started_at: createdAt,
    finished_at: null,
    error: null,
    usage: null,
    pending_question: null,
    last_seq: 0,
  };
}

const labels = (entries: ReturnType<typeof buildTimeline>) =>
  entries.map((entry) =>
    entry.kind === "message" ? entry.message.id : `run:${entry.run.id}${entry.message ? `+${entry.message.id}` : ""}`,
  );

describe("buildTimeline", () => {
  it("attaches replies to their runs, in chronological order", () => {
    const entries = buildTimeline(
      [
        message("u1", "user", "2026-01-01T10:00:00Z", "r1"),
        message("a1", "assistant", "2026-01-01T10:01:00Z", "r1"),
        message("u2", "user", "2026-01-01T10:02:00Z", "r2"),
        message("a2", "assistant", "2026-01-01T10:03:00Z", "r2"),
      ],
      [run("r1", "2026-01-01T10:00:00Z"), run("r2", "2026-01-01T10:02:00Z")],
    );
    expect(labels(entries)).toEqual(["u1", "run:r1+a1", "u2", "run:r2+a2"]);
  });

  it("places an active run without a reply after its user message, even with equal timestamps", () => {
    const entries = buildTimeline(
      [message("u1", "user", "2026-01-01T10:00:00.500Z")],
      [run("r1", "2026-01-01T10:00:00.500Z", "running")],
    );
    expect(labels(entries)).toEqual(["u1", "run:r1"]);
  });

  it("never puts a run before the user message that triggered it", () => {
    const entries = buildTimeline(
      [message("u1", "user", "2026-01-01T10:00:01Z", "r1")],
      [run("r1", "2026-01-01T10:00:00.900Z", "running")],
    );
    expect(labels(entries)).toEqual(["u1", "run:r1"]);
  });

  it("puts a retry run after the failed attempt", () => {
    const entries = buildTimeline(
      [message("u1", "user", "2026-01-01T10:00:00Z", "r1"), message("a1", "assistant", "2026-01-01T10:00:30Z", "r1")],
      [run("r1", "2026-01-01T10:00:00Z", "failed"), run("r2", "2026-01-01T10:01:00Z", "running")],
    );
    expect(labels(entries)).toEqual(["u1", "run:r1+a1", "run:r2"]);
  });

  it("compares timestamps numerically across precisions", () => {
    const entries = buildTimeline(
      [message("later", "user", "2026-01-01T10:00:00.250Z"), message("earlier", "user", "2026-01-01T10:00:00Z")],
      [],
    );
    expect(labels(entries)).toEqual(["earlier", "later"]);
  });
});
