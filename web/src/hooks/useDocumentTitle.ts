import { useEffect } from "react";

/** Sets the browser tab title ("<title> · OpenManus"). */
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · OpenManus` : "OpenManus";
  }, [title]);
}
