import type { Conversation } from "@/api/types";
import type { TranslationKey } from "@/i18n";
import { isToday } from "@/i18n/format";

export interface ConversationGroup {
  key: "pinned" | "today" | "earlier";
  label: TranslationKey;
  items: Conversation[];
}

/** Splits the (server-ordered) list into Pinned / Today / Earlier sections, dropping empty ones. */
export function groupConversations(
  conversations: readonly Conversation[],
  now: Date = new Date(),
): ConversationGroup[] {
  const pinned: Conversation[] = [];
  const today: Conversation[] = [];
  const earlier: Conversation[] = [];
  for (const conversation of conversations) {
    if (conversation.pinned) pinned.push(conversation);
    else if (isToday(conversation.updated_at, now)) today.push(conversation);
    else earlier.push(conversation);
  }
  const groups: ConversationGroup[] = [
    { key: "pinned", label: "nav.pinned", items: pinned },
    { key: "today", label: "nav.today", items: today },
    { key: "earlier", label: "nav.earlier", items: earlier },
  ];
  return groups.filter((group) => group.items.length > 0);
}
