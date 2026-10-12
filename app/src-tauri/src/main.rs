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
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, LogicalSize, Manager, State, WindowEvent};
use tauri_plugin_window_state::StateFlags;

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

const MAIN_WINDOW: &str = "main";
const WINDOW_SIZE_FILE: &str = ".window-size.json";
// Larger than any real monitor: a stored side beyond this is damage, not a preference.
const MAX_WINDOW_SIDE: f64 = 16384.0;

// A window that starts maximized reports the maximized size in a resize event before it reports being
// maximized. Resize events this soon after start are the restore, not the user, so they are not recorded.
const WINDOW_SETTLE: Duration = Duration::from_secs(3);

/// Size of the main window the last time it was neither maximized, minimized nor fullscreen, in logical pixels.
struct WindowSize {
    size: Mutex<Option<(f64, f64)>>,
    opened: Instant,
}

/// The size worth returning to, or None while the window is maximized, minimized or fullscreen.
fn plain_window_size(window: &tauri::Window) -> Option<(f64, f64)> {
    let plain = !window.is_maximized().unwrap_or(false)
        && !window.is_minimized().unwrap_or(false)
        && !window.is_fullscreen().unwrap_or(false);
    let size = window.inner_size().ok()?;
    if !plain || size.width == 0 || size.height == 0 {
        return None;
    }
    let logical = size.to_logical::<f64>(window.scale_factor().unwrap_or(1.0));
    Some((logical.width, logical.height))
}

fn window_size_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_config_dir().ok().map(|dir| dir.join(WINDOW_SIZE_FILE))
}

fn load_window_size(app: &AppHandle) -> Option<(f64, f64)> {
    let text = std::fs::read_to_string(window_size_path(app)?).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let width = value.get("width")?.as_f64()?;
    let height = value.get("height")?.as_f64()?;
    let sane = |side: f64| (1.0..=MAX_WINDOW_SIDE).contains(&side);
    (sane(width) && sane(height)).then_some((width, height))
}

fn save_window_size(app: &AppHandle) {
    let Some((width, height)) = *app.state::<WindowSize>().size.lock().unwrap() else {
        return;
    };
    let Some(path) = window_size_path(app) else {
        return;
    };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(path, serde_json::json!({ "width": width, "height": height }).to_string());
}

fn main() {
    // WebKitGTK's DMA-BUF renderer leaves blank or stale areas on some Wayland + NVIDIA (hybrid) machines.
    // This app draws only forms and tables, so the slower path costs nothing visible. Set the variable to 0 to opt out.
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    tauri::Builder::default()
        // U8: the window opens with the size, position and maximized state it was closed with
        // (Wayland compositors place windows themselves, so there only the size is kept).
        // The plugin keeps everything except the size: it stores and restores physical pixels, so the
        // window changes size whenever the display scale differs between closing and reopening (closed on
        // a 100% monitor and reopened on a 150% one, 1280x840 came back as 760x520). A stored size once
        // reached 40960x26880, which GDK cannot allocate, and the app crashed at every launch.
        // The size is kept below, in logical pixels.
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::all() & !StateFlags::SIZE)
                .build(),
        )
        .manage(WindowSize { size: Mutex::new(None), opened: Instant::now() })
        .setup(|app| {
            if let (Some(window), Some((width, height))) =
                (app.get_webview_window(MAIN_WINDOW), load_window_size(app.handle()))
            {
                *app.state::<WindowSize>().size.lock().unwrap() = Some((width, height));
                let _ = window.set_size(LogicalSize::new(width, height));
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != MAIN_WINDOW {
                return;
            }
            let state = window.state::<WindowSize>();
            match event {
                WindowEvent::Resized(_) if state.opened.elapsed() >= WINDOW_SETTLE => {
                    if let Some(size) = plain_window_size(window) {
                        *state.size.lock().unwrap() = Some(size);
                    }
                }
                WindowEvent::CloseRequested { .. } => {
                    // Also covers a resize made before the window settled.
                    if let Some(size) = plain_window_size(window) {
                        *state.size.lock().unwrap() = Some(size);
                    }
                    save_window_size(window.app_handle());
                }
                _ => {}
            }
        })
        .manage(Sidecar::default())
        .invoke_handler(tauri::generate_handler![sidecar_start, sidecar_restart, rpc_send])
        .run(tauri::generate_context!())
        .expect("error while running Odoo Dev Panel");
}
