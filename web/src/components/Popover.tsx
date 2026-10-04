import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { cn } from "@/utils/cn";

export type Placement = "bottom-start" | "bottom-end" | "top-start" | "top-end";

interface PopoverProps {
  anchorRef: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  placement?: Placement;
  /** Render as a bottom sheet on phones. */
  sheetOnMobile?: boolean;
  className?: string;
  /** Accessible name/role of the panel (e.g. a menu or listbox lives inside). */
  ariaLabel?: string;
  /** Move focus into the panel on open (dialog-like popovers). Menus manage focus themselves. */
  trapFocus?: boolean;
  children: ReactNode;
}

const MARGIN = 8;

interface Position {
  top: number;
  left: number;
  maxHeight: number;
}

function computePosition(anchor: DOMRect, panel: { width: number; height: number }, placement: Placement): Position {
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
  const spaceBelow = viewportHeight - anchor.bottom - MARGIN;
  const spaceAbove = anchor.top - MARGIN;
  let vertical = placement.startsWith("top") ? "top" : "bottom";
  if (vertical === "bottom" && panel.height > spaceBelow && spaceAbove > spaceBelow) vertical = "top";
  if (vertical === "top" && panel.height > spaceAbove && spaceBelow > spaceAbove) vertical = "bottom";
  const maxHeight = Math.max(160, (vertical === "top" ? spaceAbove : spaceBelow) - MARGIN);
  const height = Math.min(panel.height, maxHeight);
  const top = vertical === "top" ? anchor.top - height - 6 : anchor.bottom + 6;
  let left = placement.endsWith("end") ? anchor.right - panel.width : anchor.left;
  left = Math.min(Math.max(MARGIN, left), viewportWidth - panel.width - MARGIN);
  return { top, left, maxHeight };
}

/** Anchored floating panel rendered in a portal (never clipped by scroll containers). */
export function Popover(props: PopoverProps) {
  const isMobile = useIsMobile();
  if (!props.open) return null;
  return createPortal(props.sheetOnMobile && isMobile ? <Sheet {...props} /> : <Floating {...props} />, document.body);
}

function useDismiss(
  panelRef: RefObject<HTMLElement | null>,
  anchorRef: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
      anchorRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [panelRef, anchorRef, onClose]);
}

function Floating({
  anchorRef,
  onClose,
  placement = "bottom-start",
  className,
  ariaLabel,
  trapFocus,
  children,
}: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<Position | null>(null);
  useDismiss(panelRef, anchorRef, onClose);
  useFocusTrap(panelRef, Boolean(trapFocus));

  const update = useCallback(() => {
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;
    setPosition(
      computePosition(
        anchor.getBoundingClientRect(),
        { width: panel.offsetWidth, height: panel.scrollHeight },
        placement,
      ),
    );
  }, [anchorRef, placement]);

  useLayoutEffect(() => {
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [update]);

  return (
    <div
      ref={panelRef}
      aria-label={ariaLabel}
      className={cn(
        "fixed z-50 overflow-y-auto rounded-2xl border border-border bg-surface p-1.5 shadow-lg outline-none",
        position ? "animate-pop-in" : "invisible",
        className,
      )}
      style={position ? { top: position.top, left: position.left, maxHeight: position.maxHeight } : { top: 0, left: 0 }}
    >
      {children}
    </div>
  );
}

function Sheet({ anchorRef, onClose, className, ariaLabel, children }: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  useBodyScrollLock(true);
  useDismiss(panelRef, anchorRef, onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-end">
      <div className="absolute inset-0 animate-fade-in bg-overlay" aria-hidden />
      <div
        ref={panelRef}
        aria-label={ariaLabel}
        className={cn(
          "pb-safe relative max-h-[80dvh] w-full animate-slide-up overflow-y-auto rounded-t-3xl border-t border-border bg-surface px-2 pt-2 shadow-lg",
          className,
        )}
      >
        <div className="mx-auto mt-1 mb-2 h-1 w-10 rounded-full bg-border-strong" aria-hidden />
        <div className="pb-3">{children}</div>
      </div>
    </div>
  );
}
