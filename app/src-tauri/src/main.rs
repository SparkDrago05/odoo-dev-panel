// Thin shell: spawns the Python core (`odp sidecar`) and moves framed JSON-RPC
// messages between it and the web view. All Odoo logic lives in the Python core.
//
// Lifecycle: the sidecar is only a client of the per-user agents. When the app
// exits, the sidecar sees EOF on stdin and exits; Odoo processes keep running.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use tauri::{AppHandle, Emitter, State};

const INSTALLED_ODP: &str = "/usr/lib/odoo-dev-panel/bin/odp";

#[derive(Default)]
struct Sidecar {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
}

fn odp_executable() -> String {
    std::env::var("ODP_EXE").unwrap_or_else(|_| INSTALLED_ODP.to_string())
}

/// Read Content-Length framed messages until EOF and forward each body as an "rpc" event.
fn pump(app: AppHandle, stdout: impl Read) {
    let mut reader = BufReader::new(stdout);
    loop {
        let mut length: Option<usize> = None;
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => {
                    let _ = app.emit("sidecar-exit", ());
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
            let _ = app.emit("sidecar-exit", ());
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
    thread::spawn(move || pump(app, stdout));
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
    tauri::Builder::default()
        .manage(Sidecar::default())
        .invoke_handler(tauri::generate_handler![sidecar_start, rpc_send])
        .run(tauri::generate_context!())
        .expect("error while running Odoo Dev Panel");
}
