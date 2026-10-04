import { useCallback, useSyncExternalStore } from "react";

/** Subscribes to a CSS media query. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  const getSnapshot = () =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false;
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

/** Phones and small tablets: sidebar becomes a drawer (< 768px). */
export const MOBILE_QUERY = "(max-width: 767px)";
/** Files panel docks to the right from 1024px. */
export const WIDE_QUERY = "(min-width: 1024px)";
/** Touch-first devices: Enter inserts a newline in the composer. */
export const TOUCH_QUERY = "(hover: none) and (pointer: coarse)";

export const useIsMobile = () => useMediaQuery(MOBILE_QUERY);
export const useIsWide = () => useMediaQuery(WIDE_QUERY);
export const useIsTouch = () => useMediaQuery(TOUCH_QUERY);
