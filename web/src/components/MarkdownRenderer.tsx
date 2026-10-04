import { memo, useMemo, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { urls } from "@/api/client";
import { cn } from "@/utils/cn";

import { CopyButton } from "./CopyButton";

interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: { className?: unknown };
  children?: HastNode[];
}

function hastText(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(hastText).join("");
}

function codeLanguage(node: HastNode | undefined): string | null {
  const code = node?.children?.find((child) => child.tagName === "code");
  const classes = code?.properties?.className;
  const list = Array.isArray(classes) ? classes : [];
  const language = list.find((name): name is string => typeof name === "string" && name.startsWith("language-"));
  return language ? language.slice("language-".length) : null;
}

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

/** Workspace-relative paths in answers (e.g. `report.md`, `./chart.png`) point at the conversation's files. */
function resolveHref(href: string | undefined, conversationId?: string): string | undefined {
  if (!href || !conversationId) return href;
  if (SCHEME.test(href) || href.startsWith("/") || href.startsWith("#") || href.startsWith("//")) return href;
  const path = href.replace(/^\.\//, "").replace(/^workspace\//, "");
  try {
    return urls.file(conversationId, decodeURI(path));
  } catch {
    return urls.file(conversationId, path);
  }
}

function CodeBlock({ node, children }: { node?: HastNode; children?: ReactNode }) {
  const language = codeLanguage(node);
  return (
    <div className="md-code overflow-hidden rounded-xl border border-border bg-code-bg">
      <div className="flex h-9 items-center justify-between border-b border-border bg-surface-2/60 pr-1 pl-3.5">
        <span className="font-mono text-xs text-fg-subtle">{language ?? "text"}</span>
        <CopyButton text={() => hastText(node)} showText />
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export interface MarkdownProps {
  content: string;
  conversationId?: string;
  className?: string;
}

/** GitHub-flavoured markdown with highlighted, copyable code blocks. Raw HTML is never rendered. */
const MarkdownRenderer = memo(function MarkdownRenderer({ content, conversationId, className }: MarkdownProps) {
  const components = useMemo<Components>(
    () => ({
      pre: ({ node, children }) => <CodeBlock node={node as HastNode | undefined}>{children}</CodeBlock>,
      a: ({ href, children, node: _node, ...rest }: ComponentProps<"a"> & { node?: unknown }) => {
        const resolved = resolveHref(href, conversationId);
        const external = Boolean(resolved && SCHEME.test(resolved));
        return (
          <a
            {...rest}
            href={resolved}
            target={resolved?.startsWith("#") ? undefined : "_blank"}
            rel={external ? "noopener noreferrer nofollow" : "noopener"}
          >
            {children}
          </a>
        );
      },
      img: ({ src, alt, node: _node, ...rest }: ComponentProps<"img"> & { node?: unknown }) => (
        <img
          {...rest}
          src={typeof src === "string" ? resolveHref(src, conversationId) : undefined}
          alt={alt ?? ""}
          loading="lazy"
        />
      ),
      table: ({ children, node: _node, ...rest }: ComponentProps<"table"> & { node?: unknown }) => (
        <div className="md-table">
          <table {...rest}>{children}</table>
        </div>
      ),
    }),
    [conversationId],
  );

  return (
    <div className={cn("md", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false }]]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

export default MarkdownRenderer;
