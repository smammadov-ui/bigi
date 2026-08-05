// Tauri shell for bigi.
//
// Launches the bundled Python (FastAPI/uvicorn) server as a child process on a
// free local port, points its SQLite DB at the app's Application Support dir,
// waits until /health answers, then navigates the window to it. The child is
// killed when the app quits (and self-exits if ever orphaned — see the Python
// watchdog in desktop_main.py).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

struct Sidecar(Mutex<Option<Child>>);

/// Ask the OS for an unused TCP port on the loopback interface.
fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .expect("could not bind a free port")
        .local_addr()
        .unwrap()
        .port()
}

/// True once the server answers `GET /health` with a 200.
fn server_ready(port: u16) -> bool {
    use std::io::{Read, Write};
    let Ok(mut s) = std::net::TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_millis(800)));
    let _ = s.set_write_timeout(Some(Duration::from_millis(800)));
    if s.write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut buf = [0u8; 128];
    match s.read(&mut buf) {
        Ok(n) => String::from_utf8_lossy(&buf[..n]).contains(" 200 "),
        Err(_) => false,
    }
}

/// Locate the bundled `bigi-server` launcher (or a dev override via env var).
fn resolve_sidecar(app: &tauri::App) -> Option<PathBuf> {
    if let Ok(p) = std::env::var("BIGI_SERVER_BIN") {
        let pb = PathBuf::from(p);
        if pb.exists() {
            return Some(pb);
        }
    }
    let bin = if cfg!(windows) {
        "bigi-server.exe"
    } else {
        "bigi-server"
    };
    let res = app.path().resource_dir().ok()?;
    for rel in [
        format!("resources/bigi-server/{bin}"),
        format!("bigi-server/{bin}"),
        format!("_up_/resources/bigi-server/{bin}"),
    ] {
        let c = res.join(rel);
        if c.exists() {
            return Some(c);
        }
    }
    None
}

fn main() {
    tauri::Builder::default()
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;

            let port = free_port();
            let exe =
                resolve_sidecar(app).ok_or("bundled bigi-server binary not found")?;

            // A relative sqlite URL + cwd sidesteps the space in "Application
            // Support"; the DB lands at <app_data_dir>/bigi.db.
            let mut cmd = Command::new(&exe);
            cmd.current_dir(&data_dir)
                .env("BIGI_DB", "sqlite:///bigi.db")
                .args(["--host", "127.0.0.1", "--port", &port.to_string()]);
            #[cfg(windows)]
            {
                // CREATE_NO_WINDOW: the sidecar is a console-mode PyInstaller
                // binary; without this flag every launch flashes a cmd window.
                use std::os::windows::process::CommandExt;
                cmd.creation_flags(0x0800_0000);
            }
            let child = cmd.spawn()?;
            app.state::<Sidecar>().0.lock().unwrap().replace(child);

            let window = app.get_webview_window("main").ok_or("main window missing")?;
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let mut ready = false;
                for _ in 0..200 {
                    if server_ready(port) {
                        ready = true;
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                if ready {
                    if let Ok(url) =
                        tauri::Url::parse(&format!("http://127.0.0.1:{}/", port))
                    {
                        let w = window.clone();
                        let _ = handle.run_on_main_thread(move || {
                            let _ = w.navigate(url);
                        });
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                if let Some(mut child) =
                    app_handle.state::<Sidecar>().0.lock().unwrap().take()
                {
                    let _ = child.kill();
                }
            }
            _ => {}
        });
}
