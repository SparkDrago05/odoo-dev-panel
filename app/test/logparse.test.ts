import { describe, expect, it } from "vitest";
import { filterLog, parseLog } from "../src/ui/logparse";

const LOG = [
  "Starting...",
  "2026-10-08 09:00:01,101 51234 INFO ? odoo: Odoo version 19.0",
  "2026-10-08 09:04:52,733 51234 ERROR db odoo.http: Exception during request handling.",
  "Traceback (most recent call last):",
  '  File "x.py", line 1, in f',
  "AttributeError: boom",
  "2026-10-08 09:05:10,312 51234 WARNING db odoo.models: slow",
  "2026-10-08 09:05:11,000 51234 DEBUG db odoo.sql: select 1",
].join("\n");

describe("parseLog", () => {
  it("reads Odoo records and gives continuation lines the record's level", () => {
    const lines = parseLog(LOG);
    expect(lines[0]).toMatchObject({ kind: "text", level: null });
    expect(lines[1]).toMatchObject({ kind: "record", level: "INFO", logger: "odoo", db: "?" });
    expect(lines[3]).toMatchObject({ kind: "text", level: "ERROR", traceback: true });
    expect(lines[5]).toMatchObject({ kind: "text", level: "ERROR", traceback: true });
    expect(lines[6]).toMatchObject({ kind: "record", level: "WARNING", traceback: false });
  });

  it("marks job step lines and failures", () => {
    const lines = parseLog("== dump: pg_dump\nout\n== dump: FAILED exit 1", true);
    expect(lines.map((l) => l.kind)).toEqual(["step", "text", "step"]);
    expect(lines[2]).toMatchObject({ failed: true });
  });
});

describe("filterLog", () => {
  it("keeps records at the level and above with their tracebacks, and text before the first record", () => {
    const out = filterLog(LOG, "WARNING").split("\n");
    expect(out).toContain("Starting...");
    expect(out).toContain("AttributeError: boom");
    expect(out.some((l) => l.includes("INFO"))).toBe(false);
    expect(out.some((l) => l.includes("DEBUG"))).toBe(false);
    expect(out.some((l) => l.includes("WARNING"))).toBe(true);
  });
});
