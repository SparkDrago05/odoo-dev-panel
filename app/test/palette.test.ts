import { describe, expect, it, vi } from "vitest";

// The palette module imports React components; only the pure ranking is tested here.
vi.mock("../src/state/app", () => ({ useApp: () => ({}), sessionKey: () => "" }));
vi.mock("../src/shell/Sidebar", () => ({ NAV: [], installLabel: () => "" }));
const { rank } = await import("../src/shell/CommandPalette");

const cmd = (label: string, detail?: string) => ({ id: label, group: "g", label, detail, icon: null, run: () => {} });

describe("command palette ranking", () => {
  const all = [cmd("Databases"), cmd("Start nutech", "/etc/odoo/odoo19/nutech.conf"), cmd("Edit config nutech"), cmd("Doctor"), cmd("Run Odoo…")];

  it("needs every word to match", () => {
    expect(rank(all, "edit nutech").map((c) => c.label)).toEqual(["Edit config nutech"]);
    expect(rank(all, "nothing here")).toEqual([]);
  });

  it("ranks word-start matches first", () => {
    expect(rank(all, "d")[0].label).toMatch(/^D/);
  });

  it("returns everything for an empty query, in order", () => {
    expect(rank(all, "  ")).toEqual(all);
  });

  it("searches the detail line", () => {
    expect(rank(all, "odoo19").map((c) => c.label)).toEqual(["Start nutech"]);
  });
});
