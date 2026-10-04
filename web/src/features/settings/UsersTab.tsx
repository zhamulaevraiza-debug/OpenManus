import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Ellipsis, KeyRound, Plus, Shield, ShieldOff, Trash2, UserCheck, UserX } from "lucide-react";
import { useId, useRef, useState, type FormEvent } from "react";

import { api } from "@/api/client";
import { queryKeys, useMe, useUsers } from "@/api/queries";
import type { Settings, User } from "@/api/types";
import { Button, IconButton } from "@/components/Button";
import { Field, Input, Switch } from "@/components/Form";
import { Menu, type MenuItem } from "@/components/Menu";
import { ConfirmDialog, Modal, PromptDialog } from "@/components/Modal";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/toast";
import { useI18n } from "@/i18n";
import { formatShortDate } from "@/i18n/format";
import { cn } from "@/utils/cn";
import { errorMessage } from "@/utils/errors";

import { Section } from "./Section";
import { useSaveSettings } from "./useSaveSettings";

export function UsersTab({ settings }: { settings: Settings }) {
  const { t } = useI18n();
  const save = useSaveSettings();
  const { data: users, isPending } = useUsers();
  const { data: me } = useMe();
  const [creating, setCreating] = useState(false);
  const registrationLocked = settings.locked_by_env.includes("allow_registration");

  return (
    <div className="flex flex-col gap-6">
      <Section
        title={t("settings.tabs.users")}
        actions={
          <Button variant="primary" size="sm" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
            {t("settings.users.add")}
          </Button>
        }
      >
        {isPending ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : (
          <ul className="-my-2 divide-y divide-border">
            {users?.map((user) => (
              <UserRow key={user.id} user={user} isSelf={user.id === me?.id} />
            ))}
          </ul>
        )}
      </Section>
      <Section title={t("settings.users.registrationTitle")}>
        <Switch
          checked={settings.allow_registration}
          disabled={save.isPending || registrationLocked}
          onChange={(value) => save.mutate({ allow_registration: value })}
          label={t("settings.users.allowRegistration")}
          description={registrationLocked ? t("settings.lockedByEnv") : t("settings.users.allowRegistrationHint")}
        />
      </Section>
      {creating && <CreateUserDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function useUserMutation() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { t } = useI18n();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: { is_admin?: boolean; disabled?: boolean; password?: string } }) =>
      api.updateUser(id, patch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users });
      toast.success(t("settings.users.updated"));
    },
    onError: (error) => toast.error(errorMessage(t, error)),
  });
}

function UserRow({ user, isSelf }: { user: User; isSelf: boolean }) {
  const { t, language } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const update = useUserMutation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState<"password" | "delete" | null>(null);
  const anchor = useRef<HTMLButtonElement>(null);

  const items: MenuItem[] = [
    {
      key: "admin",
      label: user.is_admin ? t("settings.users.removeAdmin") : t("settings.users.makeAdmin"),
      icon: user.is_admin ? <ShieldOff /> : <Shield />,
      disabled: isSelf,
      onSelect: () => update.mutate({ id: user.id, patch: { is_admin: !user.is_admin } }),
    },
    {
      key: "disable",
      label: user.disabled ? t("settings.users.enable") : t("settings.users.disable"),
      icon: user.disabled ? <UserCheck /> : <UserX />,
      disabled: isSelf,
      onSelect: () => update.mutate({ id: user.id, patch: { disabled: !user.disabled } }),
    },
    {
      key: "password",
      label: t("settings.users.resetPassword"),
      icon: <KeyRound />,
      onSelect: () => setDialog("password"),
    },
    {
      key: "delete",
      label: t("common.delete"),
      icon: <Trash2 />,
      danger: true,
      disabled: isSelf,
      onSelect: () => setDialog("delete"),
    },
  ];

  const remove = async () => {
    try {
      await api.deleteUser(user.id);
    } catch (error) {
      toast.error(errorMessage(t, error));
      throw error;
    }
    void queryClient.invalidateQueries({ queryKey: queryKeys.users });
    toast.success(t("settings.users.deleted"));
  };

  return (
    <li className="flex items-center gap-3 py-3">
      <span
        aria-hidden
        className={cn(
          "inline-flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold uppercase",
          user.disabled ? "bg-surface-3 text-fg-subtle" : "bg-gradient-to-br from-indigo-500 to-purple-600 text-white",
        )}
      >
        {user.username.slice(0, 1)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            className={cn("truncate text-sm font-semibold", user.disabled ? "text-fg-subtle line-through" : "text-fg")}
          >
            {user.username}
          </span>
          {isSelf && (
            <span className="rounded-full bg-surface-3 px-2 py-px text-[0.6875rem] font-medium text-fg-muted">
              {t("settings.users.you")}
            </span>
          )}
          {user.is_admin && (
            <span className="rounded-full bg-accent-soft px-2 py-px text-[0.6875rem] font-medium text-accent-text">
              {t("settings.users.admin")}
            </span>
          )}
          {user.disabled && (
            <span className="rounded-full bg-danger-soft px-2 py-px text-[0.6875rem] font-medium text-danger">
              {t("settings.users.disabled")}
            </span>
          )}
        </div>
        <p className="text-xs text-fg-subtle">
          {t("settings.users.createdAt", { date: formatShortDate(user.created_at, language) })}
        </p>
      </div>
      <IconButton
        ref={anchor}
        label={t("settings.users.actions")}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        <Ellipsis />
      </IconButton>
      <Menu anchorRef={anchor} open={menuOpen} onClose={() => setMenuOpen(false)} items={items} label={user.username} />
      <PromptDialog
        open={dialog === "password"}
        title={t("settings.users.resetTitle", { username: user.username })}
        label={t("settings.account.newPassword")}
        type="password"
        minLength={8}
        autoComplete="new-password"
        submitLabel={t("common.save")}
        onSubmit={(password) => {
          if (password.length < 8) {
            toast.error(t("auth.passwordTooShort"));
            throw new Error("too short");
          }
          return update.mutateAsync({ id: user.id, patch: { password } });
        }}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        title={t("settings.users.deleteTitle")}
        body={t("settings.users.deleteBody", { username: user.username })}
        confirmLabel={t("common.delete")}
        danger
        onConfirm={remove}
        onClose={() => setDialog(null)}
      />
    </li>
  );
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const queryClient = useQueryClient();
  const formId = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.createUser({ username: username.trim(), password, is_admin: isAdmin }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users });
      toast.success(t("settings.users.created"));
      onClose();
    },
    onError: (caught) => setError(errorMessage(t, caught)),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (password.length < 8) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    setError(null);
    create.mutate();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={t("settings.users.addTitle")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            type="submit"
            form={formId}
            loading={create.isPending}
            disabled={!username.trim() || !password}
          >
            {t("settings.users.add")}
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
        <Field label={t("settings.users.username")}>
          {(props) => (
            <Input
              {...props}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
            />
          )}
        </Field>
        <Field label={t("settings.users.password")} hint={t("auth.passwordTooShort")}>
          {(props) => (
            <Input
              {...props}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
            />
          )}
        </Field>
        <Switch checked={isAdmin} onChange={setIsAdmin} label={t("settings.users.isAdmin")} />
      </form>
    </Modal>
  );
}
