import { Download, X } from "lucide-react";
import { useRef } from "react";
import { createPortal } from "react-dom";

import { useBodyScrollLock } from "@/hooks/useBodyScrollLock";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useI18n } from "@/i18n";

interface ImageViewerProps {
  src: string | null;
  alt: string;
  onClose: () => void;
}

/** Full-screen image lightbox. */
export function ImageViewer({ src, alt, onClose }: ImageViewerProps) {
  if (!src) return null;
  return createPortal(<Viewer src={src} alt={alt} onClose={onClose} />, document.body);
}

function Viewer({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  useBodyScrollLock(true);
  useFocusTrap(ref, true, { onEscape: onClose });
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
      aria-label={alt}
      className="pt-safe pb-safe fixed inset-0 z-[70] flex animate-fade-in flex-col bg-black/90"
    >
      <div className="flex shrink-0 items-center justify-end gap-2 p-3">
        <a
          href={src}
          download
          aria-label={t("common.download")}
          title={t("common.download")}
          className="inline-flex size-11 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
        >
          <Download className="size-5" />
        </a>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("common.close")}
          title={t("common.close")}
          className="inline-flex size-11 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
        >
          <X className="size-5" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center p-4 pt-0" onClick={onClose}>
        <img
          src={src}
          alt={alt}
          className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
          onClick={(event) => event.stopPropagation()}
        />
      </div>
    </div>
  );
}
