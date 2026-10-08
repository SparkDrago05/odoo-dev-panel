import { motion } from "motion/react";
import { type ReactNode, useId } from "react";

export function Tabs<T extends string>({ value, tabs, onChange, label }: {
  value: T; tabs: { id: T; label: ReactNode; icon?: ReactNode; count?: ReactNode }[]; onChange: (id: T) => void; label: string;
}) {
  const group = useId();
  const move = (e: React.KeyboardEvent, i: number) => {
    const next = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : null;
    if (next === null) return;
    e.preventDefault();
    const t = tabs[(next + tabs.length) % tabs.length];
    onChange(t.id);
    (e.currentTarget.parentElement?.children[(next + tabs.length) % tabs.length] as HTMLElement | undefined)?.focus();
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t, i) => (
        <button key={t.id} type="button" role="tab" className="tab" aria-selected={t.id === value} tabIndex={t.id === value ? 0 : -1}
          onClick={() => onChange(t.id)} onKeyDown={(e) => move(e, i)}>
          {t.icon}{t.label}{t.count}
          {t.id === value && <motion.span layoutId={`tab-${group}`} className="tab-indicator" transition={{ duration: 0.18, ease: [0.2, 0.7, 0.2, 1] }} />}
        </button>
      ))}
    </div>
  );
}
