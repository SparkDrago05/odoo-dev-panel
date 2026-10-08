import { FileText, Play, Power, PowerOff, RefreshCw, RotateCw, ScrollText, Server, ShieldAlert, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { rpc } from "../rpc";
import { Inspector, InspectorSection } from "../shell/Chrome";
import { useApp } from "../state/app";
import type { Service } from "../types";
import { LogConsole } from "../ui/LogConsole";
import { ActionMenu, ContextMenu, type MenuEntry } from "../ui/Menu";
import { Badge, Callout, Cmd, Dot, EmptyState, KV, listKeys, Loading, Segmented } from "../ui/primitives";
import { Panel, View, ViewHead } from "./common";

type Action = "start" | "stop" | "restart" | "enable" | "disable";
const tone = (s: Service) => (s.active_state === "active" ? "ok" : s.active_state === "failed" ? "bad" : "idle");

/** systemd units that run Odoo. Viewing needs no privilege; every operation asks sudo through the password prompt. */
export function ServicesView({ name }: { name?: string }) {
  const app = useApp();
  const [services, setServices] = useState<Service[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [text, setText] = useState<{ name: string; kind: "journal" | "unit"; text: string } | null>(null);
  const [loadingText, setLoadingText] = useState(false);

  const load = useCallback(async () => {
    try {
      setServices(await rpc.request<Service[]>("services.list"));
    } catch (e) {
      app.onError(String((e as Error).message));
    }
  }, [app.onError]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const selected = services?.find((s) => s.name === name) ?? null;
  const show = useCallback(async (s: Service, kind: "journal" | "unit") => {
    setLoadingText(true);
    try {
      const t = kind === "journal"
        ? (await rpc.request<{ text: string }>("services.journal", { name: s.name, lines: 400 })).text
        : (await rpc.request<{ text: string }>("services.show", { name: s.name })).text;
      setText({ name: s.name, kind, text: t });
    } catch (e) {
      app.onError(String((e as Error).message));
    } finally {
      setLoadingText(false);
    }
  }, [app.onError]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (selected && text?.name !== selected.name) show(selected, "journal"); }, [selected?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (s: Service, action: Action) => {
    setBusy(`${action} ${s.name}`);
    const ok = await app.act(`${action} ${s.name}`, () => rpc.request("services.action", { name: s.name, action }), `${s.name}: ${action} done`);
    setBusy(null);
    await load();
    if (ok && text?.name === s.name && text.kind === "journal") show(s, "journal");
  };
  const pick = (s: Service) => { app.nav({ view: "services", name: s.name }); app.inspect(); };
  const items = (s: Service): MenuEntry[] => [
    { section: "Asks for sudo" },
    s.active_state === "active" ? { label: "Stop", icon: <Square />, onSelect: () => act(s, "stop") } : { label: "Start", icon: <Play />, onSelect: () => act(s, "start") },
    { label: "Restart", icon: <RotateCw />, onSelect: () => act(s, "restart") },
    s.enabled === "enabled" ? { label: "Disable at boot", icon: <PowerOff />, onSelect: () => act(s, "disable") } : { label: "Enable at boot", icon: <Power />, onSelect: () => act(s, "enable") },
    "sep",
    { label: "Journal", icon: <ScrollText />, onSelect: () => { pick(s); show(s, "journal"); } },
    { label: "Unit file", icon: <FileText />, onSelect: () => { pick(s); show(s, "unit"); } },
  ];

  return (
    <>
      <View>
        <ViewHead icon={<Server />} title="Services" subtitle={<>systemd units whose ExecStart runs Odoo. Other units are never listed or touched.{busy && <span className="dim"> · {busy}…</span>}</>}
          actions={<button className="btn ghost" onClick={load}><RefreshCw />Refresh</button>} />
        {!services ? <Loading>Reading systemd units…</Loading> : services.length === 0 ? (
          <EmptyState icon={<Server />} title="No Odoo services">No systemd unit on this machine runs Odoo. Odoo started from this app runs as a session instead.</EmptyState>
        ) : (
          <div className="panel" style={{ overflow: "hidden" }}>
            <div className="list" role="listbox" aria-label="Services" onKeyDown={listKeys(services, selected, (a, b) => a.name === b.name, pick)}>
              {services.map((s) => (
                <ContextMenu key={s.name} items={items(s)}>
                  <div className="list-row clickable" role="option" aria-selected={selected?.name === s.name} tabIndex={selected?.name === s.name ? 0 : -1} onClick={() => pick(s)} style={{ minHeight: 52 }}>
                    <Dot tone={tone(s)} live={s.active_state === "active"} />
                    <div className="grow" style={{ display: "grid" }}>
                      <span className="strong mono truncate" style={{ fontSize: 12.5 }}>{s.name}</span>
                      <span className="meta mono xs truncate">{s.user ?? "root"} · {s.config ?? "no -c config"}</span>
                    </div>
                    <Badge tone={tone(s) === "idle" ? undefined : tone(s) as "ok"}>{s.active_state ?? "?"} · {s.sub_state ?? "?"}</Badge>
                    <Badge tone={s.enabled === "enabled" ? "accent" : undefined}>{s.enabled ?? "?"}</Badge>
                    <div className="row-actions">
                      {s.active_state === "active"
                        ? <button className="btn sm" disabled={!!busy} title="Asks for sudo" onClick={(e) => { e.stopPropagation(); act(s, "restart"); }}><RotateCw />Restart</button>
                        : <button className="btn sm" disabled={!!busy} title="Asks for sudo" onClick={(e) => { e.stopPropagation(); act(s, "start"); }}><Play />Start</button>}
                      <ActionMenu items={items(s)} />
                    </div>
                  </div>
                </ContextMenu>
              ))}
            </div>
          </div>
        )}
        {selected && (
          <Panel flush title={<div className="row"><h2>{text?.kind === "unit" ? "Unit file" : "Journal"}</h2><span className="mono xs dim">{selected.name}</span></div>}
            actions={<>
              <Segmented label="Show" value={text?.kind ?? "journal"} onChange={(k) => show(selected, k)}
                options={[{ value: "journal", label: "Journal", icon: <ScrollText /> }, { value: "unit", label: "Unit file", icon: <FileText /> }]} />
              <button className="btn ghost sm" onClick={() => show(selected, text?.kind ?? "journal")} disabled={loadingText}><RefreshCw />Reload</button>
            </>}>
            <div style={{ height: 380 }}>
              {loadingText && !text ? <Loading /> : <LogConsole text={text?.name === selected.name ? text.text : ""} levels={false} wrapDefault={text?.kind === "unit"} empty="Nothing to show." />}
            </div>
          </Panel>
        )}
      </View>
      {selected && (
        <Inspector title={<span className="mono">{selected.name}</span>}>
          <InspectorSection title="State">
            <KV items={[
              ["Active", <span className="row tight"><Dot tone={tone(selected)} />{selected.active_state} · {selected.sub_state}</span>],
              ["Result", selected.result, "mono"],
              ["At boot", selected.enabled],
              ["Runs as", selected.user ?? "root", "mono"],
            ]} />
          </InspectorSection>
          {selected.active_state === "failed" && <Callout tone="bad">The last start failed. Read the journal below for the reason.</Callout>}
          <InspectorSection title="Unit">
            <code className="xs break">{selected.path}</code>
            <span className="section-title" style={{ marginTop: 6 }}>ExecStart</span>
            <Cmd>{selected.exec_start}</Cmd>
            {selected.config && <KV items={[["Config", <code className="xs">{selected.config}</code>]]} />}
          </InspectorSection>
          <InspectorSection title="Operations">
            <span className="row tight xs dim"><ShieldAlert style={{ width: 12, height: 12 }} />Each one asks for your password (sudo systemctl).</span>
            <div className="action-stack">
              {selected.active_state === "active"
                ? <button className="btn" disabled={!!busy} onClick={() => act(selected, "stop")}><Square />Stop</button>
                : <button className="btn primary" disabled={!!busy} onClick={() => act(selected, "start")}><Play />Start</button>}
              <button className="btn" disabled={!!busy} onClick={() => act(selected, "restart")}><RotateCw />Restart</button>
              {selected.enabled === "enabled"
                ? <button className="btn" disabled={!!busy} onClick={() => act(selected, "disable")}><PowerOff />Disable at boot</button>
                : <button className="btn" disabled={!!busy} onClick={() => act(selected, "enable")}><Power />Enable at boot</button>}
            </div>
          </InspectorSection>
        </Inspector>
      )}
    </>
  );
}
