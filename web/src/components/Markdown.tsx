import { lazy, Suspense } from "react";

import { cn } from "@/utils/cn";

import type { MarkdownProps } from "./MarkdownRenderer";

// The markdown/highlighting stack is large; load it on first use and show plain text meanwhile.
const MarkdownRenderer = lazy(() => import("./MarkdownRenderer"));

export function Markdown(props: MarkdownProps) {
  return (
    <Suspense fallback={<div className={cn("md whitespace-pre-wrap", props.className)}>{props.content}</div>}>
      <MarkdownRenderer {...props} />
    </Suspense>
  );
}
