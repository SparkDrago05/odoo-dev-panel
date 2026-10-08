import { KeyRound, ShieldAlert } from "lucide-react";
import { useState } from "react";
import type { ConfirmRequest, PasswordRequest } from "../state/app";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/primitives";

const TITLE: Record<string, (user: string | null) => string> = {
  provision: () => "Create installation",
  enable: (u) => `Enable ${u}`,
  permissions: () => "Fix config permissions",
  join: () => "Join odoo-dev",
  service: () => "Manage a systemd service",
};

function explain(r: PasswordRequest): string {
  switch (r.purpose) {
    case "provision": return `sudo asks for your password once, to run the reviewed script that creates ${r.user}.`;
    case "enable": return `sudo asks for your password to run: usermod -aG odoo-dev ${r.user}.`;
    case "join": return `sudo asks for your password to run: usermod -aG odoo-dev ${r.user}.`;
    case "permissions": return `sudo asks for your password once, to run the reviewed chown/chmod script for the configs of ${r.user}.`;
    case "service": return "sudo asks for your password to run systemctl on an Odoo unit.";
    default: return `sudo asks for your password to start the agent as ${r.user}.`;
  }
}

/** sudo askpass: the password goes to sudo and is not stored. */
export function PasswordDialog({ request, onDone }: { request: PasswordRequest; onDone: (password: string | null) => void }) {
  const [password, setPassword] = useState("");
  const unlock = request.purpose === "unlock" || !request.purpose;
  return (
    <Dialog size="sm" icon={<KeyRound />} title={TITLE[request.purpose ?? ""]?.(request.user) ?? `Unlock ${request.user ?? "agent"}`}
      onClose={() => onDone(null)}
      footer={<>
        <button className="btn" type="button" onClick={() => onDone(null)}>Cancel</button>
        <button className="btn primary" type="submit" form="askpass">{unlock ? "Unlock" : "Continue"}</button>
      </>}>
      <form id="askpass" className="stack" onSubmit={(e) => { e.preventDefault(); onDone(password); }}>
        <p className="muted">{explain(request)} It is passed to sudo and not stored.</p>
        <Field label={request.prompt || "Password:"}>
          <input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </Field>
      </form>
    </Dialog>
  );
}

export function ConfirmDialog({ request, onDone }: { request: ConfirmRequest; onDone: (ok: boolean) => void }) {
  return (
    <Dialog size="sm" icon={request.danger ? <ShieldAlert /> : undefined} title={request.title} onClose={() => onDone(false)}
      footer={<>
        <button className="btn" onClick={() => onDone(false)} autoFocus>Cancel</button>
        <button className={`btn ${request.danger ? "danger solid" : "primary"}`} onClick={() => onDone(true)}>{request.confirm}</button>
      </>}>
      <div className="muted">{request.body}</div>
    </Dialog>
  );
}
