import { Markdown } from "./Markdown";

/** Fence long enough that no backtick run inside `code` can close it. */
function fenceFor(code: string): string {
  const longest = Math.max(2, ...Array.from(code.matchAll(/`+/g), (match) => match[0].length));
  return "`".repeat(longest + 1);
}

/** Highlighted, copyable code block (reuses the markdown code block renderer). */
export function CodeView({ code, language, className }: { code: string; language: string; className?: string }) {
  const fence = fenceFor(code);
  return <Markdown content={`${fence}${language}\n${code}\n${fence}`} className={className} />;
}
