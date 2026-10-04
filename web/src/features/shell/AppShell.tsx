import { useCallback, useMemo, useRef, useState, type TouchEvent } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useVisualViewportHeight } from "@/hooks/useVisualViewport";
import { useI18n } from "@/i18n";
import { Sidebar } from "@/features/conversations/Sidebar";
import { cn } from "@/utils/cn";

import { ShellContext, type ShellValue } from "./shell";

const COLLAPSED_KEY = "om.sidebarCollapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** Authenticated layout: docked sidebar (≥ 768px) or off-canvas drawer, banners, routed content. */
export function AppShell() {
  const isMobile = useIsMobile();
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sidebarCollapsed, setCollapsedState] = useState(readCollapsed);
  useVisualViewportHeight();

  // Close the drawer whenever the route changes (e.g. browser back).
  const [lastPath, setLastPath] = useState(location.pathname);
  if (lastPath !== location.pathname) {
    setLastPath(location.pathname);
    setDrawerOpen(false);
  }

  const setSidebarCollapsed = useCallback((collapsed: boolean) => {
    setCollapsedState(collapsed);
    try {
      localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch {
      // storage unavailable
    }
  }, []);

  const shell = useMemo<ShellValue>(
    () => ({
      drawerOpen: isMobile && drawerOpen,
      setDrawerOpen,
      sidebarCollapsed: !isMobile && sidebarCollapsed,
      setSidebarCollapsed,
    }),
    [isMobile, drawerOpen, sidebarCollapsed, setSidebarCollapsed],
  );

  return (
    <ShellContext.Provider value={shell}>
      <div className="flex h-[var(--app-height)] w-full overflow-hidden bg-bg">
        <a
          href="#main"
          className="sr-only z-50 rounded-lg bg-accent px-3 py-2 text-on-accent focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          <SkipLabel />
        </a>
        {isMobile ? (
          <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
        ) : (
          !sidebarCollapsed && (
            <aside className="flex w-[17rem] shrink-0 border-r border-border lg:w-[18rem]">
              <Sidebar variant="docked" onClose={() => setSidebarCollapsed(true)} onNavigate={() => undefined} />
            </aside>
          )
        )}
        <main id="main" className="flex min-w-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </ShellContext.Provider>
  );
}

function SkipLabel() {
  const { t } = useI18n();
  return <>{t("common.skipToContent")}</>;
}

function Drawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const panelRef = useRef<HTMLDivElement>(null);
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const [dragX, setDragX] = useState(0);
  useBodyScrollLock(open);
  useFocusTrap(panelRef, open, { onEscape: onClose });

  const onTouchStart = (event: TouchEvent) => {
    const touch = event.touches[0];
    touchStart.current = { x: touch.clientX, y: touch.clientY };
  };
  const onTouchMove = (event: TouchEvent) => {
    const start = touchStart.current;
    if (!start) return;
    const touch = event.touches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    if (Math.abs(dx) > Math.abs(dy)) setDragX(Math.min(0, dx));
  };
  const onTouchEnd = () => {
    if (dragX < -70) onClose();
    touchStart.current = null;
    setDragX(0);
  };

  return (
    <div className={cn("fixed inset-0 z-40", !open && "pointer-events-none")} aria-hidden={!open}>
      <div
        className={cn(
          "absolute inset-0 bg-overlay transition-opacity duration-200",
          open ? "opacity-100" : "opacity-0",
        )}
        onClick={onClose}
      />
      <div
        ref={panelRef}
        inert={!open}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={dragX ? { transform: `translateX(${dragX}px)`, transition: "none" } : undefined}
        className={cn(
          "absolute inset-y-0 left-0 flex w-[min(20rem,86vw)] shadow-lg transition-[transform,visibility] duration-250 ease-out",
          // `invisible` (applied after the slide-out) keeps the closed drawer out of the accessibility tree and hit-testing.
          open ? "visible translate-x-0" : "invisible -translate-x-full",
        )}
      >
        <Sidebar variant="drawer" onClose={onClose} onNavigate={onClose} />
      </div>
    </div>
  );
}
