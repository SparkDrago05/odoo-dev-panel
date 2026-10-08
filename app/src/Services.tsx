import { useCallback, useEffect, useState } from "react";
import { rpc } from "./rpc";

type Service = {
  name: string; path: string; user: string | null; config: string | null; exec_start: string;
  active_state: string | null; sub_state: string | null; result: string | null; enabled: string | null;
};
type Action = "start" | "stop" | "restart" | "enable" | "disable";

const dotClass = (s: Service) => (s.active_state === "active" ? "running" : s.active_state === "failed" ? "error" : "");

export default function Services({ onError }: { onError: (message: string) => void }) {
  const [services, setServices] = useState<Service[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [view, setView] = useState<{ name: string; kind: "journal" | "unit"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setServices(await rpc.request<Service[]>("services.list"));
    } catch (e) {
      onError(String((e as Error).message));
    }
  }, [onError]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (s: Service, action: Action) => {
    setBusy(`${action} ${s.name}`);
    try {
      await rpc.request("services.action", { name: s.name, action });
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setBusy(null);
      load();
    }
  };

  const open = async (s: Service, kind: "journal" | "unit") => {
    try {
      const text = kind === "journal"
        ? (await rpc.request<{ text: string }>("services.journal", { name: s.name, lines: 400 })).text
        : (await rpc.request<{ text: string }>("services.show", { name: s.name })).text;
      setView({ name: s.name, kind, text });
    } catch (e) {
      onError(String((e as Error).message));
    }
  };

  return (
    <section>
      <div className="row between">
        <h2>Services {busy && <span className="muted">· {busy}…</span>}</h2>
        <button onClick={load}>Refresh</button>
      </div>
      <p className="muted">systemd units that run Odoo. Start, stop, restart, enable and disable ask for your password through sudo.</p>
      {services && services.length === 0 && <p className="muted">No Odoo systemd units found.</p>}
      {services && services.length > 0 && (
        <table>
          <thead><tr><th>Unit</th><th>State</th><th>Boot</th><th>User</th><th>Config</th><th /></tr></thead>
          <tbody>
            {services.map((s) => (
              <tr key={s.name}>
                <td>{s.name}</td>
                <td><span className={`dot ${dotClass(s)}`} /> {s.active_state ?? "?"}<span className="muted"> · {s.sub_state ?? "?"}</span></td>
                <td>{s.enabled ?? "?"}</td>
                <td>{s.user ?? "-"}</td>
                <td><code>{s.config ?? "-"}</code></td>
                <td className="actions">
                  <div className="row end">
                    {s.active_state === "active"
                      ? <button disabled={!!busy} onClick={() => act(s, "stop")}>Stop</button>
                      : <button className="primary" disabled={!!busy} onClick={() => act(s, "start")}>Start</button>}
                    <button disabled={!!busy} onClick={() => act(s, "restart")}>Restart</button>
                    <button disabled={!!busy} onClick={() => act(s, s.enabled === "enabled" ? "disable" : "enable")}>
                      {s.enabled === "enabled" ? "Disable" : "Enable"}
                    </button>
                    <button onClick={() => open(s, "journal")}>Journal</button>
                    <button onClick={() => open(s, "unit")}>Unit file</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {view && (
        <div className="log">
          <div className="row between">
            <h3>{view.kind === "journal" ? "Journal" : "Unit file"} · {view.name}</h3>
            <div className="row">
              <button onClick={() => setView(null)}>Close</button>
            </div>
          </div>
          <pre>{view.text}</pre>
        </div>
      )}
    </section>
  );
}
