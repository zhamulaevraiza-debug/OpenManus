import { Pencil, Plug, Plus, Trash2 } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import type { McpServerConfig, Settings } from "@/api/types";
import { Button, IconButton } from "@/components/Button";
import { Field, Input, Segmented, Textarea } from "@/components/Form";
import { ConfirmDialog, Modal } from "@/components/Modal";
import { useI18n } from "@/i18n";

import { Section } from "./Section";
import { useSaveSettings } from "./useSaveSettings";

const ID_PATTERN = /^[A-Za-z0-9_-]+$/;

export function McpTab({ settings }: { settings: Settings }) {
  const { t } = useI18n();
  const save = useSaveSettings();
  const [editing, setEditing] = useState<{ id: string | null } | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const servers = settings.mcp_servers;
  const ids = Object.keys(servers).sort();

  const write = (next: Record<string, McpServerConfig>) => save.mutateAsync({ mcp_servers: next });

  return (
    <Section
      title={t("settings.tabs.mcp")}
      description={t("settings.mcp.hint")}
      actions={
        <Button variant="primary" size="sm" icon={<Plus className="size-4" />} onClick={() => setEditing({ id: null })}>
          {t("settings.mcp.add")}
        </Button>
      }
    >
      {ids.length === 0 ? (
        <div className="flex flex-col items-center py-6 text-center">
          <span className="inline-flex size-12 items-center justify-center rounded-2xl bg-surface-2 text-fg-subtle">
            <Plug className="size-6" aria-hidden />
          </span>
          <p className="mt-3 text-sm text-fg-muted">{t("settings.mcp.empty")}</p>
        </div>
      ) : (
        <ul className="-my-2 divide-y divide-border">
          {ids.map((id) => {
            const server = servers[id];
            const target = server.type === "sse" ? server.url : [server.command, ...(server.args ?? [])].join(" ");
            return (
              <li key={id} className="flex items-center gap-3 py-3">
                <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-fg-muted">
                  <Plug className="size-4" aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-fg">{id}</span>
                    <span className="shrink-0 rounded-full bg-surface-3 px-2 py-px font-mono text-[0.6875rem] text-fg-muted uppercase">
                      {server.type}
                    </span>
                  </div>
                  <p className="truncate font-mono text-xs text-fg-subtle">{target}</p>
                </div>
                <IconButton label={`${t("common.edit")} ${id}`} size="sm" onClick={() => setEditing({ id })}>
                  <Pencil />
                </IconButton>
                <IconButton label={`${t("common.remove")} ${id}`} size="sm" onClick={() => setRemoving(id)}>
                  <Trash2 />
                </IconButton>
              </li>
            );
          })}
        </ul>
      )}

      {editing && (
        <ServerDialog
          id={editing.id}
          server={editing.id ? servers[editing.id] : null}
          existingIds={ids}
          onClose={() => setEditing(null)}
          onSubmit={async (id, server) => {
            const next = { ...servers };
            if (editing.id && editing.id !== id) delete next[editing.id];
            next[id] = server;
            await write(next);
          }}
        />
      )}
      <ConfirmDialog
        open={removing !== null}
        title={t("settings.mcp.removeTitle")}
        body={removing ? t("settings.mcp.removeBody", { id: removing }) : undefined}
        confirmLabel={t("common.remove")}
        danger
        onConfirm={() => {
          if (!removing) return;
          const next = { ...servers };
          delete next[removing];
          return write(next);
        }}
        onClose={() => setRemoving(null)}
      />
    </Section>
  );
}

interface ServerDialogProps {
  id: string | null;
  server: McpServerConfig | null;
  existingIds: string[];
  onClose: () => void;
  onSubmit: (id: string, server: McpServerConfig) => Promise<unknown>;
}

function ServerDialog({ id, server, existingIds, onClose, onSubmit }: ServerDialogProps) {
  const { t } = useI18n();
  const formId = useId();
  const [serverId, setServerId] = useState(id ?? "");
  const [type, setType] = useState<McpServerConfig["type"]>(server?.type ?? "sse");
  const [url, setUrl] = useState(server?.url ?? "");
  const [command, setCommand] = useState(server?.command ?? "");
  const [args, setArgs] = useState((server?.args ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmedId = serverId.trim();
    if (!ID_PATTERN.test(trimmedId)) return setError(t("settings.mcp.idInvalid"));
    if (trimmedId !== id && existingIds.includes(trimmedId)) return setError(t("settings.mcp.idTaken"));
    if (type === "sse" && !url.trim()) return setError(t("settings.mcp.urlRequired"));
    if (type === "stdio" && !command.trim()) return setError(t("settings.mcp.commandRequired"));
    const config: McpServerConfig =
      type === "sse"
        ? { type, url: url.trim() }
        : {
            type,
            command: command.trim(),
            args: args
              .split("\n")
              .map((arg) => arg.trim())
              .filter(Boolean),
          };
    setBusy(true);
    try {
      await onSubmit(trimmedId, config);
      onClose();
    } catch {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={id ? t("settings.mcp.editTitle") : t("settings.mcp.addTitle")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" type="submit" form={formId} loading={busy}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4 pb-2" noValidate>
        {error && (
          <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        <Field label={t("settings.mcp.serverId")}>
          {(props) => (
            <Input
              {...props}
              value={serverId}
              onChange={(event) => setServerId(event.target.value)}
              placeholder="my-server"
              spellCheck={false}
              autoCapitalize="none"
            />
          )}
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-fg">{t("settings.mcp.transport")}</span>
          <Segmented
            label={t("settings.mcp.transport")}
            value={type}
            onChange={setType}
            options={[
              { value: "sse", label: t("settings.mcp.sse") },
              { value: "stdio", label: t("settings.mcp.stdio") },
            ]}
            className="w-full"
          />
        </div>
        {type === "sse" ? (
          <Field label={t("settings.mcp.url")}>
            {(props) => (
              <Input
                {...props}
                type="url"
                inputMode="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="http://localhost:8001/sse"
                spellCheck={false}
              />
            )}
          </Field>
        ) : (
          <>
            <Field label={t("settings.mcp.command")}>
              {(props) => (
                <Input
                  {...props}
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  placeholder="npx"
                  spellCheck={false}
                  autoCapitalize="none"
                />
              )}
            </Field>
            <Field label={t("settings.mcp.args")} hint={t("settings.mcp.argsHint")}>
              {(props) => (
                <Textarea
                  {...props}
                  value={args}
                  onChange={(event) => setArgs(event.target.value)}
                  placeholder={"-y\n@modelcontextprotocol/server-filesystem"}
                  className="font-mono text-sm"
                  spellCheck={false}
                />
              )}
            </Field>
          </>
        )}
      </form>
    </Modal>
  );
}
