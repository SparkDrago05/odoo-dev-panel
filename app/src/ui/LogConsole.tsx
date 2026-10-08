import { ArrowDownToLine, ChevronDown, ChevronUp, WrapText } from "lucide-react";
import { type ReactNode, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { filterLines, LEVELS, type Level, type LogLine, parseLog } from "./logparse";
import { CopyButton } from "./primitives";

const LINE = 18;
const OVERSCAN = 40;

function Line({ line, hit, current }: { line: LogLine; hit: boolean; current: boolean }) {
  const cls = `line${hit ? " hit" : ""}${current ? " current" : ""}${line.kind !== "step" && line.traceback ? " tb" : ""}`;
  if (line.kind === "record") {
    return (
      <div className={cls}>
        <span className="ts">{line.ts} {line.pid} </span>
        <span className={`lv-${line.levelText}`}>{line.levelText}</span>
        <span className="ts"> {line.db} </span>
        <span className="lg">{line.logger}:</span>
        {line.message}
      </div>
    );
  }
  if (line.kind === "step") return <div className={cls}><span className={line.failed ? "fail" : "step"}>{line.text}</span></div>;
  return <div className={cls}>{line.text || " "}</div>;
}

/** Log viewer: level colors, level filter, search, wrap, copy, follow-the-end. Without wrapping, only the visible
 * lines are rendered, so a 400 kB log streams without slowing the app down. Lines never animate. */
export function LogConsole({ text, jobs, levels = !jobs, toolbar, empty = "No output yet.", wrapDefault = !!jobs, height }: {
  text: string; jobs?: boolean; levels?: boolean; toolbar?: ReactNode; empty?: ReactNode; wrapDefault?: boolean; height?: number | string;
}) {
  const [minLevel, setMinLevel] = useState<Level | "">("");
  const [query, setQuery] = useState("");
  const [hitIndex, setHitIndex] = useState(0);
  const [wrap, setWrap] = useState(wrapDefault);
  const [view, setView] = useState({ top: 0, height: 400 });
  const [stuck, setStuck] = useState(true);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const lines = useMemo(() => filterLines(parseLog(text, jobs), minLevel), [text, jobs, minLevel]);
  const hits = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [] as number[];
    const found: number[] = [];
    lines.forEach((l, i) => { if (l.text.toLowerCase().includes(q)) found.push(i); });
    return found;
  }, [lines, query]);
  const hitSet = useMemo(() => new Set(hits), [hits]);
  const currentHit = hits.length ? hits[Math.min(hitIndex, hits.length - 1)] : -1;

  // Follow the end while the user is at the bottom.
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines.length, text]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setView({ top: el.scrollTop, height: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const jump = (i: number) => {
    const el = ref.current;
    if (!el || i < 0) return;
    stick.current = false;
    setStuck(false);
    if (!wrap) el.scrollTop = Math.max(0, i * LINE - el.clientHeight / 2);
    else el.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: "center" });
  };
  useEffect(() => { if (currentHit >= 0) jump(currentHit); }, [currentHit, query]); // eslint-disable-line react-hooks/exhaustive-deps

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    stick.current = atEnd;
    if (atEnd !== stuck) setStuck(atEnd);
    if (!wrap) setView({ top: el.scrollTop, height: el.clientHeight });
  };

  const toEnd = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = true;
    setStuck(true);
    el.scrollTop = el.scrollHeight;
  };

  const first = wrap ? 0 : Math.max(0, Math.floor(view.top / LINE) - OVERSCAN);
  const last = wrap ? lines.length : Math.min(lines.length, Math.ceil((view.top + view.height) / LINE) + OVERSCAN);

  return (
    <div className="term-wrap" style={height !== undefined ? { height } : undefined}>
      <div className="term-bar">
        {toolbar}
        <span className="grow" />
        {levels && (
          <select value={minLevel} onChange={(e) => setMinLevel(e.target.value as Level | "")} aria-label="Minimum level">
            <option value="">All levels</option>
            {LEVELS.filter((l) => l !== "DEBUG").map((l) => <option key={l} value={l}>{l} and above</option>)}
          </select>
        )}
        <input type="search" placeholder="Search output" value={query} aria-label="Search output"
          onChange={(e) => { setQuery(e.target.value); setHitIndex(0); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && hits.length) { e.preventDefault(); setHitIndex((i) => (e.shiftKey ? (i - 1 + hits.length) % hits.length : (i + 1) % hits.length)); }
            if (e.key === "Escape" && query) { e.stopPropagation(); setQuery(""); }
          }} />
        {query && (
          <>
            <span className="xs dim mono" style={{ minWidth: 44, textAlign: "center" }}>{hits.length ? `${Math.min(hitIndex, hits.length - 1) + 1}/${hits.length}` : "0/0"}</span>
            <button type="button" className="btn ghost icon sm" aria-label="Previous match" disabled={!hits.length}
              onClick={() => setHitIndex((i) => (i - 1 + hits.length) % hits.length)}><ChevronUp /></button>
            <button type="button" className="btn ghost icon sm" aria-label="Next match" disabled={!hits.length}
              onClick={() => setHitIndex((i) => (i + 1) % hits.length)}><ChevronDown /></button>
          </>
        )}
        <button type="button" className={`btn ghost icon sm${wrap ? " active" : ""}`} aria-pressed={wrap} aria-label="Wrap lines" title="Wrap lines"
          onClick={() => setWrap(!wrap)}><WrapText /></button>
        <CopyButton text={lines.map((l) => l.text).join("\n")} label="Copy output" />
        <button type="button" className="btn ghost icon sm" aria-label="Scroll to end" title="Follow the end" disabled={stuck} onClick={toEnd}><ArrowDownToLine /></button>
      </div>
      <div ref={ref} className={`term${wrap ? " wrap" : ""}`} onScroll={onScroll} tabIndex={0} role="log" aria-label="Output">
        {lines.length === 0 ? <div className="term-empty">{empty}</div> : wrap ? (
          lines.map((l, i) => <div key={i} data-i={i} style={{ display: "contents" }}><Line line={l} hit={hitSet.has(i)} current={i === currentHit} /></div>)
        ) : (
          <div style={{ height: lines.length * LINE, position: "relative", minWidth: "max-content" }}>
            <div style={{ position: "absolute", top: first * LINE, left: 0, right: 0 }}>
              {lines.slice(first, last).map((l, k) => <Line key={first + k} line={l} hit={hitSet.has(first + k)} current={first + k === currentHit} />)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
