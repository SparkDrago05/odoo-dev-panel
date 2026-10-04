// JSON-RPC client for the Python sidecar. Rust only moves framed messages;
// requests, responses and notifications are handled here.
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

type Json = any; // eslint-disable-line @typescript-eslint/no-explicit-any
type RequestHandler = (params: Json) => Promise<Json>;
type NotificationHandler = (params: Json) => void;

export class RpcError extends Error {
  constructor(public code: number, message: string, public data?: Json) {
    super(message);
  }
}

class Rpc {
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: Json) => void; reject: (e: Error) => void }>();
  private requestHandlers = new Map<string, RequestHandler>();
  private listeners = new Map<string, Set<NotificationHandler>>();
  private ready: Promise<void>;

  constructor() {
    this.ready = this.init();
  }

  private async init() {
    await listen<string>("rpc", (event) => this.onMessage(JSON.parse(event.payload)));
    await listen("sidecar-exit", () => {
      for (const { reject } of this.pending.values()) reject(new Error("sidecar exited"));
      this.pending.clear();
      this.emit("sidecar.exit", {});
    });
    await invoke("sidecar_start");
  }

  /** New core process, for example after joining the odoo-dev group. Pending requests fail; Odoo keeps running. */
  async restart() {
    await this.ready;
    for (const { reject } of this.pending.values()) reject(new Error("core restarted"));
    this.pending.clear();
    await invoke("sidecar_restart");
  }

  private async send(message: Json) {
    await invoke("rpc_send", { message: JSON.stringify(message) });
  }

  async request<T = Json>(method: string, params?: Json): Promise<T> {
    await this.ready;
    const id = this.nextId++;
    const result = new Promise<T>((resolve, reject) => this.pending.set(id, { resolve, reject }));
    await this.send({ jsonrpc: "2.0", id, method, params: params ?? null });
    return result;
  }

  handle(method: string, handler: RequestHandler) {
    this.requestHandlers.set(method, handler);
  }

  on(method: string, handler: NotificationHandler): () => void {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set());
    this.listeners.get(method)!.add(handler);
    return () => this.listeners.get(method)!.delete(handler);
  }

  private emit(method: string, params: Json) {
    this.listeners.get(method)?.forEach((h) => h(params));
  }

  private async onMessage(msg: Json) {
    if (msg.method !== undefined) {
      if (msg.id === undefined || msg.id === null) {
        this.emit(msg.method, msg.params);
        return;
      }
      const handler = this.requestHandlers.get(msg.method);
      try {
        if (!handler) throw new RpcError(-32601, `unknown method: ${msg.method}`);
        const result = await handler(msg.params);
        await this.send({ jsonrpc: "2.0", id: msg.id, result: result ?? null });
      } catch (e) {
        const err = e as RpcError;
        await this.send({ jsonrpc: "2.0", id: msg.id, error: { code: err.code ?? -32603, message: String(err.message) } });
      }
      return;
    }
    const pending = this.pending.get(msg.id);
    if (!pending) return;
    this.pending.delete(msg.id);
    if (msg.error) pending.reject(new RpcError(msg.error.code, msg.error.message, msg.error.data));
    else pending.resolve(msg.result);
  }
}

export const rpc = new Rpc();
