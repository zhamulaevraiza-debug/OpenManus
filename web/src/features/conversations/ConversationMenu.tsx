import { Download, Pencil, Pin, PinOff, Trash2 } from "lucide-react";
import { useState, type RefObject } from "react";

import { urls } from "@/api/client";
import type { Conversation } from "@/api/types";
import { Menu, type MenuItem } from "@/components/Menu";
import { ConfirmDialog, PromptDialog } from "@/components/Modal";
import { useI18n } from "@/i18n";

import { useConversationActions } from "./useConversationActions";

interface ConversationMenuProps {
  conversation: Conversation;
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
}

/** Rename / pin / export / delete menu with its dialogs. */
export function ConversationMenu({ conversation, anchorRef, open, onClose }: ConversationMenuProps) {
  const { t } = useI18n();
  const actions = useConversationActions();
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const title = conversation.title || t("nav.untitled");

  const items: MenuItem[] = [
    { key: "rename", label: t("common.rename"), icon: <Pencil />, onSelect: () => setDialog("rename") },
    {
      key: "pin",
      label: conversation.pinned ? t("nav.unpin") : t("nav.pin"),
      icon: conversation.pinned ? <PinOff /> : <Pin />,
      onSelect: () => void actions.togglePin(conversation).catch(() => undefined),
    },
    {
      key: "export",
      label: t("nav.exportMarkdown"),
      icon: <Download />,
      href: urls.export(conversation.id),
      download: true,
    },
    { key: "delete", label: t("common.delete"), icon: <Trash2 />, danger: true, onSelect: () => setDialog("delete") },
  ];

  return (
    <>
      <Menu anchorRef={anchorRef} open={open} onClose={onClose} items={items} label={t("nav.chatActions")} />
      <PromptDialog
        open={dialog === "rename"}
        title={t("nav.renameTitle")}
        label={t("nav.titleLabel")}
        initialValue={title}
        maxLength={200}
        submitLabel={t("common.save")}
        onSubmit={(value) => actions.rename(conversation, value)}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        title={t("nav.deleteTitle")}
        body={t("nav.deleteBody", { title })}
        confirmLabel={t("common.delete")}
        danger
        onConfirm={() => actions.remove(conversation)}
        onClose={() => setDialog(null)}
      />
    </>
  );
}
