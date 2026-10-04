import { Ellipsis, PanelLeftClose, Pin, Search, Settings, SquarePen, X } from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useMatch } from "react-router-dom";

import { useConversations, useMe } from "@/api/queries";
import type { Conversation } from "@/api/types";
import { IconButton } from "@/components/Button";
import { LogoMark } from "@/components/Logo";
import { Skeleton } from "@/components/Skeleton";
import { useI18n } from "@/i18n";
import { cn } from "@/utils/cn";

import { ConversationMenu } from "./ConversationMenu";
import { groupConversations } from "./grouping";

interface SidebarProps {
  /** Mobile drawer variant shows a close button instead of collapse. */
  variant: "drawer" | "docked";
  onClose: () => void;
  onNavigate: () => void;
}

export function Sidebar({ variant, onClose, onNavigate }: SidebarProps) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const { data: me } = useMe();

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  const search = useDeferredValue(debounced);
  const { data, isPending, isError, refetch } = useConversations(search);
  const groups = useMemo(() => groupConversations(data ?? []), [data]);

  return (
    <nav
      aria-label={t("nav.mainNavigation")}
      data-testid="sidebar"
      className="pt-safe pl-safe flex h-full w-full flex-col bg-sidebar"
    >
      <div className="flex h-14 shrink-0 items-center gap-2 pr-2 pl-4">
        <Link to="/" onClick={onNavigate} className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg">
          <LogoMark className="size-7" />
          <span className="truncate text-[0.9375rem] font-semibold tracking-tight">{t("common.appName")}</span>
        </Link>
        {variant === "drawer" ? (
          <IconButton label={t("nav.closeSidebar")} onClick={onClose}>
            <X />
          </IconButton>
        ) : (
          <IconButton label={t("nav.collapseSidebar")} size="sm" onClick={onClose}>
            <PanelLeftClose />
          </IconButton>
        )}
      </div>

      <div className="flex shrink-0 flex-col gap-2 px-3 pb-2">
        <Link
          to="/"
          onClick={onNavigate}
          data-testid="new-chat"
          className="flex h-10 items-center gap-2.5 rounded-xl border border-border bg-surface px-3 text-sm font-medium text-fg shadow-xs transition-colors hover:bg-surface-2 max-md:h-12"
        >
          <SquarePen className="size-4 text-fg-muted" aria-hidden />
          {t("nav.newChat")}
        </Link>
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-subtle"
            aria-hidden
          />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("nav.searchChats")}
            aria-label={t("nav.searchChats")}
            className="h-10 w-full rounded-xl border border-transparent bg-surface-2 pr-9 pl-9 text-sm text-fg placeholder:text-fg-subtle focus:border-accent focus:bg-surface focus:outline-none max-md:h-12 [&::-webkit-search-cancel-button]:hidden"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label={t("nav.clearSearch")}
              className="absolute top-1/2 right-1.5 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-lg text-fg-subtle hover:bg-surface-3 hover:text-fg"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="scroll-area min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {isPending ? (
          <div className="flex flex-col gap-2 px-2 pt-3" aria-busy="true">
            {Array.from({ length: 7 }, (_, index) => (
              <Skeleton key={index} className={cn("h-8", index % 3 === 0 ? "w-3/4" : "w-full")} />
            ))}
          </div>
        ) : isError ? (
          <div className="px-3 pt-4 text-sm text-fg-muted">
            <p>{t("errors.generic")}</p>
            <button
              type="button"
              onClick={() => refetch()}
              className="mt-2 font-medium text-accent-text hover:underline"
            >
              {t("common.retry")}
            </button>
          </div>
        ) : groups.length === 0 ? (
          <p className="px-3 pt-6 text-center text-sm text-fg-subtle">
            {search ? t("nav.noResults") : t("nav.noChats")}
          </p>
        ) : (
          groups.map((group) => (
            <section key={group.key} aria-labelledby={`group-${group.key}`} className="mt-3 first:mt-1">
              <h2
                id={`group-${group.key}`}
                className="flex items-center gap-1.5 px-3 pb-1 text-xs font-medium text-fg-subtle"
              >
                {group.key === "pinned" && <Pin className="size-3" aria-hidden />}
                {t(group.label)}
              </h2>
              <ul className="flex flex-col gap-px">
                {group.items.map((conversation) => (
                  <ConversationItem key={conversation.id} conversation={conversation} onNavigate={onNavigate} />
                ))}
              </ul>
            </section>
          ))
        )}
      </div>

      <div className="pb-safe shrink-0 border-t border-border p-2">
        <Link
          to="/settings"
          onClick={onNavigate}
          data-testid="settings-link"
          className="flex min-h-12 items-center gap-3 rounded-xl px-2.5 py-2 transition-colors hover:bg-surface-2"
        >
          <span
            aria-hidden
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 text-sm font-semibold text-white uppercase"
          >
            {me?.username.slice(0, 1) ?? "·"}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-fg">{me?.username}</span>
            <span className="block truncate text-xs text-fg-subtle">
              {me?.is_admin ? t("settings.account.roleAdmin") : t("settings.account.roleUser")}
            </span>
          </span>
          <Settings className="size-[18px] shrink-0 text-fg-subtle" aria-label={t("nav.settings")} />
        </Link>
      </div>
    </nav>
  );
}

function ConversationItem({ conversation, onNavigate }: { conversation: Conversation; onNavigate: () => void }) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const active = Boolean(useMatch(`/c/${conversation.id}`));
  const running = Boolean(conversation.active_run_id);

  return (
    <li className="group relative">
      <NavLink
        to={`/c/${conversation.id}`}
        onClick={onNavigate}
        data-testid="conversation-item"
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex h-9 items-center gap-2 rounded-lg pr-10 pl-3 text-sm transition-colors max-md:h-11",
          active ? "bg-surface-3 font-medium text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
        )}
      >
        {running && (
          <span className="relative flex size-2 shrink-0" title={t("nav.working")}>
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60 motion-reduce:hidden" />
            <span className="relative inline-flex size-2 rounded-full bg-accent" />
            <span className="sr-only">{t("nav.working")}</span>
          </span>
        )}
        <span className="truncate">{conversation.title || t("nav.untitled")}</span>
      </NavLink>
      <button
        ref={menuButton}
        type="button"
        aria-label={t("nav.chatActions")}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
        className={cn(
          "absolute top-1/2 right-1 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-fg-subtle transition-opacity hover:bg-surface-3 hover:text-fg max-md:size-9",
          "focus-visible:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100",
          (menuOpen || active) && "[@media(hover:hover)]:opacity-100",
        )}
      >
        <Ellipsis className="size-4" />
      </button>
      <ConversationMenu
        conversation={conversation}
        anchorRef={menuButton}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />
    </li>
  );
}
