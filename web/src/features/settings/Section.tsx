import type { ReactNode } from "react";

import { cn } from "@/utils/cn";

interface SectionProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A titled settings card. */
export function Section({ title, description, actions, children, className }: SectionProps) {
  return (
    <section className={cn("rounded-2xl border border-border bg-surface shadow-xs", className)}>
      <header className="flex flex-wrap items-start gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1 basis-52">
          <h2 className="text-[0.9375rem] font-semibold text-fg">{title}</h2>
          {description && <p className="mt-0.5 text-sm leading-relaxed text-fg-muted">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      <div className="px-5 py-5">{children}</div>
    </section>
  );
}
