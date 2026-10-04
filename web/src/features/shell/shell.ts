import { createContext, useContext } from "react";

export interface ShellValue {
  /** Mobile off-canvas drawer. */
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  /** Desktop sidebar collapsed to give the content full width. */
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

export const ShellContext = createContext<ShellValue | null>(null);

export function useShell(): ShellValue {
  const value = useContext(ShellContext);
  if (!value) throw new Error("useShell must be used inside <AppShell>");
  return value;
}
