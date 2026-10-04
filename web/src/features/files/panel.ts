import { createContext, useContext } from "react";

export interface FilesPanelControl {
  open: boolean;
  /** Opens the panel, optionally previewing `path`. */
  show: (path?: string) => void;
  hide: () => void;
  toggle: () => void;
}

export const FilesPanelContext = createContext<FilesPanelControl | null>(null);

export function useFilesPanel(): FilesPanelControl | null {
  return useContext(FilesPanelContext);
}
