export const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
export type Level = (typeof LEVELS)[number];

// Same record shape as core/src/odoo_dev_panel/logs.py: "2024-05-01 10:00:00,123 4242 INFO db logger: message".
const RECORD = /^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (\d+) ([A-Z_]+) (\S+) ([^\s:]+):(.*)$/;

export type LogLine =
  | { kind: "record"; text: string; level: Level | null; ts: string; pid: string; levelText: string; db: string; logger: string; message: string; traceback: boolean }
  | { kind: "text"; text: string; level: Level | null; traceback: boolean }
  | { kind: "step"; text: string; failed: boolean };

/** Split a log into lines with their level. Lines after a record (tracebacks) take the record's level. */
export function parseLog(text: string, jobs = false): LogLine[] {
  const out: LogLine[] = [];
  let level: Level | null = null;
  let traceback = false;
  for (const line of text.split("\n")) {
    if (jobs && line.startsWith("== ")) {
      out.push({ kind: "step", text: line, failed: /: FAILED/.test(line) });
      continue;
    }
    const m = RECORD.exec(line);
    if (m) {
      level = (LEVELS as readonly string[]).includes(m[3]) ? (m[3] as Level) : null;
      traceback = false;
      out.push({ kind: "record", text: line, level, ts: m[1], pid: m[2], levelText: m[3], db: m[4], logger: m[5], message: m[6], traceback });
      continue;
    }
    if (line.startsWith("Traceback (most recent call last)")) traceback = true;
    out.push({ kind: "text", text: line, level, traceback });
  }
  if (out.length && out[out.length - 1].text === "") out.pop();
  return out;
}

/** Keep records at `minimum` or above with their continuation lines (tracebacks); keep output before the first record. */
export function filterLog(text: string, minimum: Level): string {
  return filterLines(parseLog(text), minimum).map((l) => l.text).join("\n");
}

export function filterLines(lines: LogLine[], minimum: Level | ""): LogLine[] {
  if (!minimum || minimum === "DEBUG") return lines;
  const min = LEVELS.indexOf(minimum);
  return lines.filter((l) => l.kind === "step" || l.level === null || LEVELS.indexOf(l.level) >= min);
}

/** A Python traceback frame: `  File "/path/x.py", line 12, in func`. Groups: indent, file, ", line ", line, rest. */
export const FRAME = /^(\s*File ")(\/[^"]+)(", line )(\d+)(.*)$/;
