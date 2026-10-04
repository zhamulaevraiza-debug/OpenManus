import { useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "@/api/client";
import { queryKeys, useMe } from "@/api/queries";
import { Button } from "@/components/Button";
import { Field, Input } from "@/components/Form";
import { useToast } from "@/components/toast";
import { useI18n } from "@/i18n";
import { errorMessage } from "@/utils/errors";

import { Section } from "./Section";

export function AccountTab() {
  const { t } = useI18n();
  const { data: me } = useMe();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    if (next.length < 8) return setError(t("auth.passwordTooShort"));
    if (next !== confirm) return setError(t("auth.passwordsMismatch"));
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success(t("settings.account.passwordChanged"));
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 400 ? caught.message : errorMessage(t, caught));
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    setSigningOut(true);
    try {
      await api.logout();
    } catch {
      // the session is dropped locally either way
    }
    queryClient.clear();
    queryClient.setQueryData(queryKeys.me, null);
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex flex-col gap-6">
      <Section title={t("settings.tabs.account")}>
        <div className="flex flex-wrap items-center gap-4">
          <span
            aria-hidden
            className="inline-flex size-12 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 text-lg font-semibold text-white uppercase"
          >
            {me?.username.slice(0, 1)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-fg-subtle">{t("settings.account.signedInAs")}</p>
            <p className="truncate text-base font-semibold text-fg">{me?.username}</p>
            <p className="text-sm text-fg-muted">
              {me?.is_admin ? t("settings.account.roleAdmin") : t("settings.account.roleUser")}
            </p>
          </div>
          <Button variant="outline" icon={<LogOut className="size-4" />} loading={signingOut} onClick={signOut}>
            {t("settings.account.signOut")}
          </Button>
        </div>
      </Section>
      <Section title={t("settings.account.changePassword")}>
        <form onSubmit={changePassword} className="flex max-w-md flex-col gap-4" noValidate>
          {error && (
            <p role="alert" className="rounded-xl bg-danger-soft px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}
          <Field label={t("settings.account.currentPassword")}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            )}
          </Field>
          <Field label={t("settings.account.newPassword")} hint={t("auth.passwordTooShort")}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
              />
            )}
          </Field>
          <Field label={t("settings.account.confirmNewPassword")}>
            {(props) => (
              <Input
                {...props}
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
            )}
          </Field>
          <div>
            <Button type="submit" variant="primary" loading={busy} disabled={!current || !next || !confirm}>
              {t("settings.account.updatePassword")}
            </Button>
          </div>
        </form>
      </Section>
    </div>
  );
}
