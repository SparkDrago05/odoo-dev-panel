import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";

/** Modal dialog. Escape closes it unless `locked` (a job is running); clicks outside never close it, so typed input
 * is not lost by a stray click. Focus is trapped inside and returns to the opener on close. */
export function Dialog({ title, subtitle, icon, size = "md", onClose, locked, footer, children, initialFocus }: {
  title: ReactNode; subtitle?: ReactNode; icon?: ReactNode; size?: "sm" | "md" | "lg" | "xl";
  onClose: () => void; locked?: boolean; footer?: ReactNode; children: ReactNode; initialFocus?: boolean;
}) {
  return (
    <RD.Root open onOpenChange={(open) => { if (!open && !locked) onClose(); }}>
      <RD.Portal>
        <RD.Overlay asChild>
          <motion.div className="overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.16 }} />
        </RD.Overlay>
        <RD.Content
          asChild
          aria-describedby={undefined}
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => { if (locked) e.preventDefault(); }}
          onOpenAutoFocus={(e) => {
            // Focus the first field of the body (or a button in the footer), not the close button in the header.
            e.preventDefault();
            if (initialFocus === false) return;
            const root = e.currentTarget as HTMLElement;
            const target = root.querySelector<HTMLElement>("[autofocus], [data-autofocus]")
              ?? root.querySelector<HTMLElement>(".dialog-body input:not([type=checkbox]):not([disabled]), .dialog-body textarea, .dialog-body select")
              ?? root.querySelector<HTMLElement>(".dialog-foot .btn.primary:not(:disabled), .dialog-foot .btn:not(:disabled)")
              ?? root;
            target.focus();
          }}
        >
          <motion.div
            className={`dialog ${size}`}
            initial={{ opacity: 0, x: "-50%", y: 8, scale: 0.985 }}
            animate={{ opacity: 1, x: "-50%", y: 0, scale: 1 }}
            transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}
          >
            <div className="dialog-head">
              {icon && <span className="head-icon" style={{ width: 30, height: 30, borderRadius: 8 }}>{icon}</span>}
              <div className="grow" style={{ display: "grid" }}>
                <RD.Title asChild><h2>{title}</h2></RD.Title>
                {subtitle && <span className="sub truncate">{subtitle}</span>}
              </div>
              <button type="button" className="btn ghost icon sm" aria-label="Close" title={locked ? "Wait for the job to finish" : "Close (Esc)"}
                disabled={locked} onClick={onClose}><X /></button>
            </div>
            <div className="dialog-body">{children}</div>
            {footer && <div className="dialog-foot">{footer}</div>}
          </motion.div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}
