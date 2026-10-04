import { useId } from "react";

import { cn } from "@/utils/cn";

/** The OpenManus mark (same artwork as the app icons). */
export function LogoMark({ className }: { className?: string }) {
  // Unique per instance: a gradient defined inside a hidden copy would not paint the others.
  const gradientId = `om-logo-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg viewBox="0 0 512 512" className={cn("size-8 shrink-0 rounded-[25%]", className)} aria-hidden>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6366F1" />
          <stop offset="1" stopColor="#9333EA" />
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="128" fill={`url(#${gradientId})`} />
      <path
        d="M148 360V196l108 104 108-104v164"
        fill="none"
        stroke="#fff"
        strokeWidth="46"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="256" cy="150" r="30" fill="#fff" />
    </svg>
  );
}
