import { Bot, CircleUser, Cpu, Info, Palette, Plug, Search, Users, type LucideIcon } from "lucide-react";
import { Navigate, NavLink, useParams } from "react-router-dom";

import { useMe, useSettings } from "@/api/queries";
import { Button } from "@/components/Button";
import { Skeleton } from "@/components/Skeleton";
import { AboutTab } from "@/features/settings/AboutTab";
import { AccountTab } from "@/features/settings/AccountTab";
import { AgentsTab } from "@/features/settings/AgentsTab";
import { AppearanceTab } from "@/features/settings/AppearanceTab";
import { McpTab } from "@/features/settings/McpTab";
import { ModelTab } from "@/features/settings/ModelTab";
import { SearchTab } from "@/features/settings/SearchTab";
import { UsersTab } from "@/features/settings/UsersTab";
import { TopBar } from "@/features/shell/TopBar";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useI18n, type TranslationKey } from "@/i18n";
import { cn } from "@/utils/cn";

type TabId = "account" | "appearance" | "model" | "search" | "agents" | "mcp" | "users" | "about";

interface TabDef {
  id: TabId;
  icon: LucideIcon;
  admin: boolean;
}

const TABS: TabDef[] = [
  { id: "account", icon: CircleUser, admin: false },
  { id: "appearance", icon: Palette, admin: false },
  { id: "model", icon: Cpu, admin: true },
  { id: "search", icon: Search, admin: true },
  { id: "agents", icon: Bot, admin: true },
  { id: "mcp", icon: Plug, admin: true },
  { id: "users", icon: Users, admin: true },
  { id: "about", icon: Info, admin: false },
];

/** Settings with tabs; administrators additionally see model, search, agents, MCP and users. */
export function SettingsPage() {
  const { t } = useI18n();
  const { tab } = useParams();
  const { data: me } = useMe();
  const isAdmin = Boolean(me?.is_admin);
  const tabs = TABS.filter((item) => isAdmin || !item.admin);
  const active = tabs.find((item) => item.id === tab);
  useDocumentTitle(t("settings.title"));

  if (!active) return <Navigate to="/settings/account" replace />;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TopBar title={<h1 className="truncate text-[0.9375rem] font-semibold text-fg">{t("settings.title")}</h1>} />
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <nav
          aria-label={t("settings.sections")}
          className="shrink-0 border-b border-border md:w-60 md:border-r md:border-b-0 md:py-4 lg:w-64"
        >
          <ul className="flex [scrollbar-width:none] gap-1 overflow-x-auto px-3 py-2 md:flex-col md:overflow-visible md:py-0">
            {tabs.map(({ id, icon: Icon }) => (
              <li key={id} className="shrink-0">
                <NavLink
                  to={`/settings/${id}`}
                  replace
                  className={({ isActive }) =>
                    cn(
                      "flex h-10 items-center gap-2.5 rounded-xl px-3 text-sm font-medium whitespace-nowrap transition-colors max-md:h-11 max-md:rounded-full",
                      isActive
                        ? "bg-surface-2 text-fg max-md:bg-fg max-md:text-bg"
                        : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                    )
                  }
                >
                  <Icon className="size-4 shrink-0" aria-hidden />
                  {t(`settings.tabs.${id}` as TranslationKey)}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="scroll-area min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-4 pt-5 pb-10 md:px-8 md:pt-8">
            <TabContent tab={active.id} admin={active.admin} />
          </div>
        </div>
      </div>
    </div>
  );
}

function TabContent({ tab, admin }: { tab: TabId; admin: boolean }) {
  const { t } = useI18n();
  const settings = useSettings(admin);

  if (tab === "account") return <AccountTab />;
  if (tab === "appearance") return <AppearanceTab />;
  if (tab === "about") return <AboutTab />;

  if (settings.isPending) {
    return (
      <div className="flex flex-col gap-6" aria-busy="true">
        <Skeleton className="h-56 w-full rounded-2xl" />
        <Skeleton className="h-40 w-full rounded-2xl" />
      </div>
    );
  }
  if (settings.isError || !settings.data) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-2xl border border-border bg-surface p-5">
        <p className="text-sm text-fg-muted">{t("settings.loadFailed")}</p>
        <Button variant="outline" size="sm" onClick={() => settings.refetch()}>
          {t("common.retry")}
        </Button>
      </div>
    );
  }
  const data = settings.data;
  switch (tab) {
    case "model":
      return <ModelTab settings={data} />;
    case "search":
      return <SearchTab settings={data} />;
    case "agents":
      return <AgentsTab settings={data} />;
    case "mcp":
      return <McpTab settings={data} />;
    case "users":
      return <UsersTab settings={data} />;
  }
}
