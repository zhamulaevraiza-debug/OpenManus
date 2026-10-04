import { Menu as MenuIcon, PanelLeftOpen } from "lucide-react";
import type { ReactNode } from "react";

import { IconButton } from "@/components/Button";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { Banners } from "./Banners";
import { useShell } from "./shell";

interface TopBarProps {
  title?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

/** Page header: sidebar toggle, title, page actions and app-wide banners. */
export function TopBar({ title, actions, className }: TopBarProps) {
  const { t } = useI18n();
  const shell = useShell();
  return (
    <header
      className={cn(
        "pt-safe sticky top-0 z-20 shrink-0 border-b border-border/70 bg-bg/85 backdrop-blur-md supports-[backdrop-filter]:bg-bg/70",
        className,
      )}
    >
      <div className="flex h-14 items-center gap-1 px-2 md:px-3">
        <IconButton
          label={t("nav.openSidebar")}
          data-testid="sidebar-toggle"
          aria-expanded={shell.drawerOpen}
          className="md:hidden"
          onClick={() => shell.setDrawerOpen(true)}
        >
          <MenuIcon />
        </IconButton>
        {shell.sidebarCollapsed && (
          <IconButton
            label={t("nav.expandSidebar")}
            className="max-md:hidden"
            onClick={() => shell.setSidebarCollapsed(false)}
          >
            <PanelLeftOpen />
          </IconButton>
        )}
        <div className="min-w-0 flex-1 px-1.5">{title}</div>
        {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </div>
      <Banners />
    </header>
  );
}
