import { useEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from "react";

import { cn } from "@/utils/cn";

import { Popover, type Placement } from "./Popover";

export interface MenuItem {
  key: string;
  label: string;
  icon?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  /** Renders a link (e.g. a download) instead of a button. */
  href?: string;
  download?: boolean;
  onSelect?: () => void;
}

interface MenuProps {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  items: MenuItem[];
  label: string;
  placement?: Placement;
}

/** Action menu (WAI-ARIA menu pattern): arrow keys, Home/End, Enter/Space, Escape. */
export function Menu({ anchorRef, open, onClose, items, label, placement = "bottom-end" }: MenuProps) {
  return (
    <Popover
      anchorRef={anchorRef}
      open={open}
      onClose={onClose}
      placement={placement}
      sheetOnMobile
      className="min-w-52"
    >
      <MenuList items={items} label={label} onClose={onClose} />
    </Popover>
  );
}

function MenuList({ items, label, onClose }: { items: MenuItem[]; label: string; onClose: () => void }) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>("[role=menuitem]:not([aria-disabled=true])")?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const elements = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not([aria-disabled=true])") ?? [],
    );
    if (elements.length === 0) return;
    const index = elements.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = (index + 1) % elements.length;
    else if (event.key === "ArrowUp") next = (index - 1 + elements.length) % elements.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = elements.length - 1;
    else if (event.key === "Tab") onClose();
    if (next !== null) {
      event.preventDefault();
      elements[next].focus();
    }
  };

  return (
    <div ref={listRef} role="menu" aria-label={label} onKeyDown={onKeyDown} className="flex flex-col">
      {items.map((item) => {
        const className = cn(
          "flex min-h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-sm font-medium transition-colors outline-none max-md:min-h-12 max-md:text-[0.9375rem]",
          "hover:bg-surface-2 focus-visible:bg-surface-2 [&_svg]:size-4 [&_svg]:shrink-0",
          item.danger ? "text-danger" : "text-fg",
          item.disabled && "pointer-events-none opacity-40",
        );
        const content = (
          <>
            <span className={cn(item.danger ? "text-danger" : "text-fg-muted")}>{item.icon}</span>
            <span className="truncate">{item.label}</span>
          </>
        );
        if (item.href) {
          return (
            <a
              key={item.key}
              role="menuitem"
              tabIndex={-1}
              href={item.href}
              download={item.download || undefined}
              className={className}
              // Close after the browser has acted on the link: a detached anchor would not navigate.
              onClick={() => setTimeout(onClose)}
            >
              {content}
            </a>
          );
        }
        return (
          <button
            key={item.key}
            type="button"
            role="menuitem"
            tabIndex={-1}
            aria-disabled={item.disabled || undefined}
            className={className}
            onClick={() => {
              onClose();
              item.onSelect?.();
            }}
          >
            {content}
          </button>
        );
      })}
    </div>
  );
}
