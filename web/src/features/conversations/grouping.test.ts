import { describe, expect, it } from "vitest";

import type { Conversation } from "@/api/types";

import { groupConversations } from "./grouping";

function conversation(id: string, updatedAt: string, pinned = false): Conversation {
  return {
    id,
    title: id,
    mode: "auto",
    pinned,
    created_at: updatedAt,
    updated_at: updatedAt,
    last_message_preview: null,
    active_run_id: null,
  };
}

describe("groupConversations", () => {
  it("splits into Pinned / Today / Earlier and drops empty groups", () => {
    const now = new Date(2026, 4, 10, 15, 0, 0);
    const today = new Date(2026, 4, 10, 9, 0, 0).toISOString();
    const lastWeek = new Date(2026, 4, 3, 9, 0, 0).toISOString();
    const groups = groupConversations(
      [conversation("p", lastWeek, true), conversation("t", today), conversation("e", lastWeek)],
      now,
    );
    expect(groups.map((group) => [group.key, group.items.map((item) => item.id)])).toEqual([
      ["pinned", ["p"]],
      ["today", ["t"]],
      ["earlier", ["e"]],
    ]);
    expect(groupConversations([conversation("t", today)], now).map((group) => group.key)).toEqual(["today"]);
  });
});
