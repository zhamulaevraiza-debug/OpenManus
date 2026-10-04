import { createElement } from "react";

import { fileIcon } from "./fileTypes";

/** Icon for a file or folder by name/type. */
export function FileIcon({
  name,
  isDir = false,
  mime = null,
  className,
}: {
  name: string;
  isDir?: boolean;
  mime?: string | null;
  className?: string;
}) {
  return createElement(fileIcon(name, isDir, mime), { className, "aria-hidden": true });
}
