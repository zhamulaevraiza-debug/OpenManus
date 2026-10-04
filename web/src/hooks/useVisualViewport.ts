import { useEffect } from "react";

/**
 * Keeps `--app-height` equal to the visible viewport while an on-screen keyboard overlays the page
 * (iOS Safari does not resize the layout viewport), so the composer stays above the keyboard.
 */
export function useVisualViewportHeight() {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const root = document.documentElement;
    const update = () => {
      // Ignore pinch-zoom: only an unzoomed, shorter visual viewport means an on-screen keyboard.
      const keyboardOpen = Math.abs(viewport.scale - 1) < 0.01 && viewport.height < window.innerHeight - 1;
      if (keyboardOpen) {
        root.style.setProperty("--app-height", `${Math.round(viewport.height)}px`);
        if (window.scrollY !== 0) window.scrollTo(0, 0);
      } else {
        root.style.removeProperty("--app-height");
      }
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      root.style.removeProperty("--app-height");
    };
  }, []);
}
