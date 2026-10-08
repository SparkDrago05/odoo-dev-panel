import type { ReactNode } from "react";

/** Scrollable center column with a header. */
export function View({ children, full }: { children: ReactNode; full?: boolean }) {
  return (
    <div className="view">
      <div className={`view-inner${full ? " full" : ""}`}>{children}</div>
    </div>
  );
}

export function ViewHead({ icon, title, subtitle, actions }: { icon?: ReactNode; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="view-head">
      {icon && <span className="head-icon">{icon}</span>}
      <div className="titles">
        <h1>{title}</h1>
        {subtitle && <div className="subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function Panel({ title, actions, children, flush }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean }) {
  return (
    <section className="panel">
      {(title || actions) && <div className="panel-head">{typeof title === "string" ? <h2>{title}</h2> : title}<span className="grow" />{actions}</div>}
      <div className={`panel-body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}
