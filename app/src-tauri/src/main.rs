// Thin shell: spawns the Python core (`odp sidecar`) and moves framed JSON-RPC
// messages between it and the web view. All Odoo logic lives in the Python core.
//
// Lifecycle: the sidecar is only a client of the per-user agents. When the app
// exits, the sidecar sees EOF on stdin and exits; Odoo processes keep running.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::thread;

use tauri::{AppHandle, Emitter, Manager, State};

const INSTALLED_ODP: &str = "/usr/lib/odoo-dev-panel/bin/odp";

#[derive(Default)]
struct Sidecar {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    // Bumped on every spawn: the reader of a replaced core must not report its exit as the current one's.
    generation: AtomicU64,
}

fn odp_executable() -> String {
    std::env::var("ODP_EXE").unwrap_or_else(|_| INSTALLED_ODP.to_string())
}

/// Read Content-Length framed messages until EOF and forward each body as an "rpc" event.
fn exited(app: &AppHandle, generation: u64) {
    if app.state::<Sidecar>().generation.load(Ordering::SeqCst) == generation {
        let _ = app.emit("sidecar-exit", ());
    }
}

fn pump(app: AppHandle, stdout: impl Read, generation: u64) {
    let mut reader = BufReader::new(stdout);
    loop {
        let mut length: Option<usize> = None;
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => {
                    exited(&app, generation);
                    return;
                }
                Ok(_) => {}
            }
            let line = line.trim_end();
            if line.is_empty() {
                break;
            }
            if let Some((name, value)) = line.split_once(':') {
                if name.trim().eq_ignore_ascii_case("content-length") {
                    length = value.trim().parse().ok();
                }
            }
        }
        let Some(length) = length else { continue };
        let mut body = vec![0u8; length];
        if reader.read_exact(&mut body).is_err() {
            exited(&app, generation);
            return;
        }
        let _ = app.emit("rpc", String::from_utf8_lossy(&body).into_owned());
    }
}

#[tauri::command]
fn sidecar_start(app: AppHandle, state: State<Sidecar>) -> Result<(), String> {
    let mut child_slot = state.child.lock().unwrap();
    if let Some(child) = child_slot.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Ok(()); // already running (for example after a web view reload)
        }
    }
    spawn_sidecar(app, &state, &mut child_slot)
}

/// Stop the core and start a new one, for example after the user joined the odoo-dev group: the new core
/// takes the group through `sg`. Agents and Odoo processes do not depend on the core and keep running.
#[tauri::command]
fn sidecar_restart(app: AppHandle, state: State<Sidecar>) -> Result<(), String> {
    let mut child_slot = state.child.lock().unwrap();
    *state.stdin.lock().unwrap() = None;
    if let Some(mut child) = child_slot.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    spawn_sidecar(app, &state, &mut child_slot)
}

fn spawn_sidecar(app: AppHandle, state: &State<Sidecar>, child_slot: &mut Option<Child>) -> Result<(), String> {
    let exe = odp_executable();
    let mut child = Command::new(&exe)
        .arg("sidecar")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("cannot start core {exe}: {e}"))?;
    let stdout = child.stdout.take().ok_or("no sidecar stdout")?;
    *state.stdin.lock().unwrap() = child.stdin.take();
    *child_slot = Some(child);
    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    thread::spawn(move || pump(app, stdout, generation));
    Ok(())
}

#[tauri::command]
fn rpc_send(state: State<Sidecar>, message: String) -> Result<(), String> {
    let mut guard = state.stdin.lock().unwrap();
    let stdin = guard.as_mut().ok_or("core is not running")?;
    let frame = format!("Content-Length: {}\r\n\r\n", message.len());
    stdin
        .write_all(frame.as_bytes())
        .and_then(|_| stdin.write_all(message.as_bytes()))
        .and_then(|_| stdin.flush())
        .map_err(|e| format!("core write failed: {e}"))
}

fn main() {
    // WebKitGTK's DMA-BUF renderer leaves blank or stale areas on some Wayland + NVIDIA (hybrid) machines.
    // This app draws only forms and tables, so the slower path costs nothing visible. Set the variable to 0 to opt out.
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    tauri::Builder::default()
        .manage(Sidecar::default())
        .invoke_handler(tauri::generate_handler![sidecar_start, sidecar_restart, rpc_send])
        .run(tauri::generate_context!())
        .expect("error while running Odoo Dev Panel");
}
