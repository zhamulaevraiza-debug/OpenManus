import { useQueryClient } from "@tanstack/react-query";
import { BarChart3, Code, Eye, EyeOff, Globe, PenLine, Telescope } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { api, ApiError } from "@/api/client";
import { queryKeys, useAuthConfig, useMe } from "@/api/queries";
import { Button } from "@/components/Button";
import { Field, Input } from "@/components/Form";
import { LogoMark } from "@/components/Logo";
import { PreferenceToggles } from "@/features/shell/PreferenceToggles";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";
import { errorMessage } from "@/utils/errors";

interface LocationState {
  from?: string;
  expired?: boolean;
}

const SHOWCASE = [
  { icon: Telescope, key: "researcher", tone: "bg-amber-400/20 text-amber-200" },
  { icon: Code, key: "coder", tone: "bg-emerald-400/20 text-emerald-200" },
  { icon: Globe, key: "browser", tone: "bg-sky-400/20 text-sky-200" },
  { icon: BarChart3, key: "data_analyst", tone: "bg-violet-400/25 text-violet-100" },
  { icon: PenLine, key: "writer", tone: "bg-rose-400/20 text-rose-200" },
] as const;

/** Sign-in and (when enabled) registration. */
export function LoginPage({ mode }: { mode: "login" | "register" }) {
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const { data: authConfig } = useAuthConfig();
  const state = (location.state ?? {}) as LocationState;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(state.expired ? t("auth.sessionExpired") : null);
  const [busy, setBusy] = useState(false);
  const formId = useId();
  const registering = mode === "register";

  if (me) return <Navigate to={state.from ?? "/"} replace />;
  if (registering && authConfig && !authConfig.allow_registration) return <Navigate to="/login" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (registering && password.length < 8) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    if (registering && password !== confirm) {
      setError(t("auth.passwordsMismatch"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const user = registering
        ? await api.register(username.trim(), password)
        : await api.login(username.trim(), password);
      queryClient.clear();
      queryClient.setQueryData(queryKeys.me, user);
      navigate(state.from ?? "/", { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) setError(t("auth.invalidCredentials"));
      else if (caught instanceof ApiError && caught.status === 429) setError(t("auth.tooManyAttempts"));
      else if (caught instanceof ApiError && caught.status === 403 && registering)
        setError(t("auth.registrationDisabled"));
      else setError(errorMessage(t, caught));
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-[var(--app-height)] bg-bg">
      <aside className="relative hidden w-[44%] max-w-xl flex-col justify-between overflow-hidden bg-zinc-950 p-10 text-white lg:flex">
        <div
          className="pointer-events-none absolute -top-32 -left-24 size-[28rem] rounded-full bg-indigo-600/40 blur-3xl"
          aria-hidden
        />
        <div
          className="pointer-events-none absolute -right-24 -bottom-40 size-[30rem] rounded-full bg-purple-600/30 blur-3xl"
          aria-hidden
        />
        <div className="relative flex items-center gap-3">
          <LogoMark className="size-9" />
          <span className="text-lg font-semibold tracking-tight">{t("common.appName")}</span>
        </div>
        <div className="relative">
          <p className="max-w-md text-3xl leading-tight font-semibold tracking-tight text-balance">
            {t("auth.tagline")}
          </p>
          <ul className="mt-8 flex flex-wrap gap-2">
            {SHOWCASE.map(({ icon: Icon, key, tone }) => (
              <li
                key={key}
                className={cn(
                  "inline-flex h-9 items-center gap-2 rounded-full px-3.5 text-sm font-medium backdrop-blur",
                  tone,
                )}
              >
                <Icon className="size-4" aria-hidden />
                {t(`agents.${key}.name`)}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-sm text-white/50">© OpenManus</p>
      </aside>

      <main className="pt-safe pb-safe flex min-w-0 flex-1 flex-col">
        <div className="flex justify-end p-3">
          <PreferenceToggles />
        </div>
        <div className="flex flex-1 items-center justify-center px-5 pb-16">
          <div className="w-full max-w-sm">
            <div className="mb-8 flex flex-col items-center text-center lg:items-start lg:text-left">
              <LogoMark className="mb-5 size-12 lg:hidden" />
              <h1 className="text-2xl font-semibold tracking-tight text-fg">
                {registering ? t("auth.registerTitle") : t("auth.signInTitle")}
              </h1>
              <p className="mt-1.5 text-sm text-fg-muted">
                {registering ? t("auth.registerSubtitle") : t("auth.signInSubtitle")}
              </p>
            </div>

            <form id={formId} onSubmit={submit} className="flex flex-col gap-4" noValidate>
              {error && (
                <div
                  role="alert"
                  className="rounded-xl border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm text-danger"
                >
                  {error}
                </div>
              )}
              <Field label={t("auth.username")}>
                {(props) => (
                  <Input
                    {...props}
                    data-testid="login-username"
                    autoComplete="username"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    required
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                  />
                )}
              </Field>
              <Field label={t("auth.password")} hint={registering ? t("auth.passwordTooShort") : undefined}>
                {(props) => (
                  <div className="relative">
                    <Input
                      {...props}
                      data-testid="login-password"
                      type={showPassword ? "text" : "password"}
                      autoComplete={registering ? "new-password" : "current-password"}
                      required
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      className="pr-12"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((value) => !value)}
                      aria-label={showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
                      title={showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
                      className="absolute top-1/2 right-1 inline-flex size-9 -translate-y-1/2 items-center justify-center rounded-lg text-fg-subtle hover:bg-surface-2 hover:text-fg"
                    >
                      {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                    </button>
                  </div>
                )}
              </Field>
              {registering && (
                <Field label={t("auth.confirmPassword")}>
                  {(props) => (
                    <Input
                      {...props}
                      type={showPassword ? "text" : "password"}
                      autoComplete="new-password"
                      required
                      value={confirm}
                      onChange={(event) => setConfirm(event.target.value)}
                    />
                  )}
                </Field>
              )}
              <Button
                type="submit"
                variant="primary"
                size="lg"
                data-testid="login-submit"
                loading={busy}
                disabled={!username.trim() || !password}
                className="mt-2 w-full"
              >
                {registering
                  ? busy
                    ? t("auth.registering")
                    : t("auth.register")
                  : busy
                    ? t("auth.signingIn")
                    : t("auth.signIn")}
              </Button>
            </form>

            {(registering || authConfig?.allow_registration) && (
              <p className="mt-6 text-center text-sm text-fg-muted">
                {registering ? t("auth.haveAccount") : t("auth.noAccount")}{" "}
                <Link
                  to={registering ? "/login" : "/register"}
                  className="font-semibold text-accent-text underline-offset-2 hover:underline"
                >
                  {registering ? t("auth.signIn") : t("auth.register")}
                </Link>
              </p>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
