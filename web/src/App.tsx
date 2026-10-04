import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { setUnauthorizedHandler } from "@/api/client";
import { queryKeys, useMe } from "@/api/queries";
import { LogoMark } from "@/components/Logo";
import { Spinner } from "@/components/Spinner";
import { AppShell } from "@/features/shell/AppShell";
import { useI18n } from "@/i18n";
import { ChatRoute } from "@/pages/ChatPage";
import { LoginPage } from "@/pages/LoginPage";

const SettingsPage = lazy(() => import("@/pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));

function Splash() {
  const { t } = useI18n();
  return (
    <div className="flex h-[var(--app-height)] flex-col items-center justify-center gap-4 bg-bg" aria-busy="true">
      <LogoMark className="size-12 animate-pulse" />
      <Spinner className="size-5 text-fg-subtle" label={t("common.loading")} />
    </div>
  );
}

/** Sends any 401 from the API back to the sign-in page with a clean cache. */
function UnauthorizedRedirect() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (queryClient.getQueryData(queryKeys.me) === null) return;
      queryClient.clear();
      queryClient.setQueryData(queryKeys.me, null);
      const from = `${window.location.pathname}${window.location.search}`;
      navigate("/login", { replace: true, state: { from, expired: true } });
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient, navigate, location.key]);
  return null;
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending, isError, refetch } = useMe();
  const location = useLocation();
  const { t } = useI18n();
  if (isPending) return <Splash />;
  if (isError) {
    return (
      <div className="flex h-[var(--app-height)] flex-col items-center justify-center gap-3 bg-bg px-6 text-center">
        <LogoMark className="size-12" />
        <p className="text-sm text-fg-muted">{t("errors.network")}</p>
        <button
          type="button"
          onClick={() => refetch()}
          className="text-sm font-semibold text-accent-text hover:underline"
        >
          {t("common.retry")}
        </button>
      </div>
    );
  }
  if (!me) return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  return <>{children}</>;
}

function NotFound() {
  return <Navigate to="/" replace />;
}

export function App() {
  return (
    <>
      <UnauthorizedRedirect />
      <Routes>
        <Route path="/login" element={<LoginPage mode="login" />} />
        <Route path="/register" element={<LoginPage mode="register" />} />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<ChatRoute />} />
          <Route path="c/:conversationId" element={<ChatRoute />} />
          <Route
            path="settings/:tab?"
            element={
              <Suspense fallback={<div className="flex-1" />}>
                <SettingsPage />
              </Suspense>
            }
          />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}
