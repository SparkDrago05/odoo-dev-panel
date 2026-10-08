import * as CM from "@radix-ui/react-context-menu";
import * as DM from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal } from "lucide-react";
import type { ReactNode } from "react";

export type MenuEntry =
  | { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean; hint?: string }
  | { section: string }
  | "sep"
  | null
  | false;

function clean(items: MenuEntry[]) {
  const list = items.filter(Boolean) as Exclude<MenuEntry, null | false>[];
  // No separator at the ends or twice in a row.
  return list.filter((e, i) => e !== "sep" || (i > 0 && i < list.length - 1 && list[i - 1] !== "sep"));
}

function Items({ items, kit }: { items: MenuEntry[]; kit: typeof DM | typeof CM }) {
  return (
    <>
      {clean(items).map((e, i) =>
        e === "sep" ? <kit.Separator key={i} className="menu-sep" />
        : "section" in e ? <kit.Label key={i} className="menu-label">{e.section}</kit.Label>
        : (
          <kit.Item key={i} className={`menu-item${e.danger ? " danger" : ""}`} disabled={e.disabled} onSelect={() => e.onSelect()}>
            {e.icon}{e.label}{e.hint && <span className="hint">{e.hint}</span>}
          </kit.Item>
        ),
      )}
    </>
  );
}

/** "…" button with secondary actions. Always visible, so nothing important hides behind hover. */
export function ActionMenu({ items, label = "More actions", trigger, align = "end" }: {
  items: MenuEntry[]; label?: string; trigger?: ReactNode; align?: "start" | "end";
}) {
  return (
    <DM.Root modal={false}>
      <DM.Trigger asChild>
        {trigger ?? (
          <button type="button" className="btn ghost icon sm" aria-label={label} title={label} onClick={(e) => e.stopPropagation()}>
            <MoreHorizontal />
          </button>
        )}
      </DM.Trigger>
      <DM.Portal>
        <DM.Content className="menu" align={align} sideOffset={4} collisionPadding={8} onClick={(e) => e.stopPropagation()}>
          <Items items={items} kit={DM} />
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

/** Right-click menu with the same entries as the row's action menu. */
export function ContextMenu({ items, children }: { items: MenuEntry[]; children: ReactNode }) {
  return (
    <CM.Root modal={false}>
      <CM.Trigger asChild>{children}</CM.Trigger>
      <CM.Portal>
        <CM.Content className="menu" collisionPadding={8}>
          <Items items={items} kit={CM} />
        </CM.Content>
      </CM.Portal>
    </CM.Root>
  );
}
