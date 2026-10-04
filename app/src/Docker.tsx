import { useEffect, useState } from "react";
import { rpc } from "./rpc";

type Place = { container: string; host: string | null; volume: string | null; anonymous: boolean; in_image: boolean };
type Container = {
  id: string; name: string; image: string; version: string | null; status: string | null; running: boolean;
  compose: { project: string; service: string | null; working_dir: string | null; files: string[] } | null;
  ports: { container: string; host_ip: string | null; host_port: number }[];
  config: Place | null; addons: Place[]; data: Place | null;
  db: { host: string | null; port: string | null; user: string | null; container: string | null };
};
type Listing = { containers: Container[]; error: string | null; available: boolean };

const where = (p: Place) => p.host ?? (p.volume ? `${p.anonymous ? "anonymous volume" : "volume"} ${p.volume}` : "inside the image");

/** Odoo containers, read-only. Loaded on its own so a slow Docker daemon does not hold up the scan. */
export function Docker({ onError }: { onError: (message: string) => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setListing(await rpc.request<Listing>("docker.list"));
    } catch (e) {
      onError(String((e as Error).message));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Hide the section on machines without Docker; it is not a problem there.
  if (listing && !listing.available && listing.error === "docker is not installed") return null;
  return (
    <>
      <div className="row">
        <h3>Docker</h3>
        <button onClick={load} disabled={loading}>{loading ? "Reading…" : "Refresh"}</button>
      </div>
      {listing?.error && <p className="muted">Docker: {listing.error}</p>}
      {listing && !listing.error && listing.containers.length === 0 && <p className="muted">No Odoo containers.</p>}
      {listing && listing.containers.length > 0 && (
        <table>
          <thead>
            <tr><th>Container</th><th>Odoo</th><th>State</th><th>Ports</th><th>Mounts</th></tr>
          </thead>
          <tbody>
            {listing.containers.map((c) => (
              <tr key={c.id}>
                <td>
                  {c.name}
                  <div className="muted">{c.image}{c.compose ? ` · compose ${c.compose.project}/${c.compose.service ?? "?"}` : ""}</div>
                </td>
                <td>{c.version ?? "?"}</td>
                <td><span className={`dot ${c.running ? "running" : ""}`} /> {c.status}</td>
                <td>
                  {c.ports.length === 0 ? <span className="muted">none</span> : c.ports.map((p) => (
                    <div key={`${p.host_port}-${p.container}`}>
                      {c.running && p.container.startsWith("8069/")
                        ? <a href="#" onClick={(e) => { e.preventDefault(); rpc.request("run.open", { port: p.host_port }).catch((err) => onError(String(err.message))); }}>:{p.host_port}</a>
                        : `:${p.host_port}`}
                      <span className="muted"> → {p.container}</span>
                    </div>
                  ))}
                </td>
                <td className="muted">
                  {c.config && <div>config {c.config.container} = {where(c.config)}</div>}
                  {c.addons.map((a) => <div key={a.container}>addons {a.container} = {where(a)}</div>)}
                  {c.data && <div>data {where(c.data)}</div>}
                  {(c.db.container || c.db.host) && <div>db {c.db.container ?? c.db.host}{c.db.user ? ` as ${c.db.user}` : ""}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
