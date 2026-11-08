use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::io;
use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime};
use tauri::{Manager, RunEvent, State};
use uuid::Uuid;

const IDENTITY_PROTOCOL_VERSION: u16 = 1;
const DEFAULT_API_BASE: &str = "http://127.0.0.1:8000";

#[derive(Debug, Deserialize, Clone)]
struct IdentityResponse {
    product: String,
    component: String,
    protocol_version: u16,
    ready: bool,
    server_id: String,
    pid: u32,
    launch_id: Option<String>,
    launch_proof: Option<String>,
}

#[derive(Debug, Serialize)]
struct ServerStatus {
    api_base: String,
    managed: bool,
    server_id: String,
    pid: u32,
}

struct ManagedServer {
    child: Child,
    api_base: String,
    launch_id: String,
    launch_secret: String,
    server_id: String,
}

#[derive(Default)]
struct ServerManager {
    managed: Mutex<Option<ManagedServer>>,
}

fn identity_url(api_base: &str) -> String {
    format!("{api_base}/agentlearn/identity")
}

fn probe(api_base: &str, timeout: Duration) -> Result<Option<IdentityResponse>, String> {
    let response = ureq::AgentBuilder::new()
        .build()
        .get(&identity_url(api_base))
        .timeout(timeout)
        .call();
    match response {
        Ok(response) => response
            .into_json::<IdentityResponse>()
            .map(Some)
            .map_err(|error| format!("identity response invalid: {error}")),
        Err(ureq::Error::Transport(_)) => Ok(None),
        Err(ureq::Error::Status(_, _)) => Err("port is occupied by a non-AgentLearn service".into()),
    }
}

fn is_agentlearn(identity: &IdentityResponse) -> bool {
    identity.product == "agentlearn"
        && identity.component == "desktop-api"
        && identity.protocol_version == IDENTITY_PROTOCOL_VERSION
        && identity.ready
}

fn launch_proof(launch_id: &str, secret: &str, pid: u32) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes())
        .expect("HMAC accepts any key length");
    mac.update(
        format!("{launch_id}:{pid}:{IDENTITY_PROTOCOL_VERSION}").as_bytes(),
    );
    hex::encode(mac.finalize().into_bytes())
}

fn is_managed(
    identity: &IdentityResponse,
    launch_id: &str,
    launch_secret: &str,
    child_pid: u32,
) -> bool {
    identity.launch_id.as_deref() == Some(launch_id)
        && identity.pid == child_pid
        && identity.launch_proof.as_deref()
            == Some(&launch_proof(launch_id, launch_secret, child_pid))
}

fn choose_loopback_base() -> Result<String, io::Error> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    Ok(format!("http://127.0.0.1:{port}"))
}

fn spawn_server(api_base: &str, launch_id: &str, launch_secret: &str) -> Result<Child, String> {
    if !cfg!(debug_assertions) {
        return Err(
            "managed server startup is dev-only until the release server bundle is defined".into(),
        );
    }

    let port = api_base
        .rsplit(':')
        .next()
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| format!("invalid loopback API base: {api_base}"))?;
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let server_dir = std::path::Path::new(manifest_dir)
        .join("../../server")
        .canonicalize()
        .map_err(|error| format!("server directory not found: {error}"))?;

    let python = server_dir.join(".venv/bin/python");
    if !python.exists() {
        return Err(format!(
            "server Python not found: {}; run `uv sync` in server/ first",
            python.display()
        ));
    }

    Command::new(python)
        .arg("main.py")
        .arg("desktop")
        .arg("--port")
        .arg(port.to_string())
        .current_dir(server_dir)
        .env("AGENTLEARN_LAUNCH_ID", launch_id)
        .env("AGENTLEARN_LAUNCH_SECRET", launch_secret)
        .stdin(Stdio::null())
        .spawn()
        .map_err(|error| format!("failed to start server: {error}"))
}

fn status_from_identity(api_base: &str, identity: &IdentityResponse, managed: bool) -> ServerStatus {
    ServerStatus {
        api_base: api_base.to_string(),
        managed,
        server_id: identity.server_id.clone(),
        pid: identity.pid,
    }
}

fn kill_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn wait_for_managed(
    mut child: Child,
    api_base: &str,
    launch_id: &str,
    launch_secret: &str,
) -> Result<ManagedServer, String> {
    let child_pid = child.id();
    let deadline = SystemTime::now() + Duration::from_secs(10);
    while SystemTime::now() < deadline {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("server exited before readiness: {status}"));
        }
        match probe(api_base, Duration::from_millis(250)) {
            Ok(Some(identity)) => {
                if !is_agentlearn(&identity) {
                    kill_child(&mut child);
                    return Err("server returned an incompatible identity".into());
                }
                if !is_managed(&identity, launch_id, launch_secret, child_pid) {
                    kill_child(&mut child);
                    return Err("server did not prove this Tauri launch".into());
                }
                return Ok(ManagedServer {
                    child,
                    api_base: api_base.to_string(),
                    launch_id: launch_id.to_string(),
                    launch_secret: launch_secret.to_string(),
                    server_id: identity.server_id,
                });
            }
            Ok(None) => thread::sleep(Duration::from_millis(150)),
            Err(message) => {
                kill_child(&mut child);
                return Err(message);
            }
        }
    }
    kill_child(&mut child);
    Err("server did not become ready within 10 seconds".into())
}

fn start_managed(api_base: &str) -> Result<ManagedServer, String> {
    let launch_id = Uuid::new_v4().to_string();
    let launch_secret = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let child = spawn_server(api_base, &launch_id, &launch_secret)?;
    match wait_for_managed(child, api_base, &launch_id, &launch_secret) {
        Ok(managed) => Ok(managed),
        Err(message) => Err(message),
    }
}

#[tauri::command(rename_all = "snake_case")]
async fn ensure_server(
    preferred_base: Option<String>,
    allow_fallback: bool,
    state: State<'_, ServerManager>,
) -> Result<ServerStatus, String> {
    let preferred_base = preferred_base.unwrap_or_else(|| DEFAULT_API_BASE.to_string());

    let existing = state
        .managed
        .lock()
        .map_err(|_| "server manager poisoned")?
        .take();
    if let Some(mut managed) = existing {
        if let Ok(None) = managed.child.try_wait() {
            if let Ok(Some(identity)) = probe(&managed.api_base, Duration::from_millis(600)) {
                if is_agentlearn(&identity)
                    && is_managed(
                        &identity,
                        &managed.launch_id,
                        &managed.launch_secret,
                        managed.child.id(),
                    )
                {
                    let status = status_from_identity(&managed.api_base, &identity, true);
                    *state
                        .managed
                        .lock()
                        .map_err(|_| "server manager poisoned")? = Some(managed);
                    return Ok(status);
                }
            }
        }
        kill_child(&mut managed.child);
    }

    if let Ok(Some(identity)) = probe(&preferred_base, Duration::from_millis(600)) {
        if !is_agentlearn(&identity) {
            return Err("API base has an incompatible AgentLearn server".into());
        }
        return Ok(status_from_identity(&preferred_base, &identity, false));
    }

    let managed = match start_managed(&preferred_base) {
        Ok(managed) => managed,
        Err(first_error) => {
            if !allow_fallback {
                return Err(first_error);
            }
            let fallback = choose_loopback_base()
                .map_err(|error| format!("{first_error}; fallback port failed: {error}"))?;
            start_managed(&fallback)?
        }
    };

    let status = ServerStatus {
        api_base: managed.api_base.clone(),
        managed: true,
        server_id: managed.server_id.clone(),
        pid: managed.child.id(),
    };
    *state.managed.lock().map_err(|_| "server manager poisoned")? = Some(managed);
    Ok(status)
}

fn shutdown_managed(state: &ServerManager) {
    if let Ok(mut guard) = state.managed.lock() {
        if let Some(mut managed) = guard.take() {
            kill_child(&mut managed.child);
        }
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            app.manage(ServerManager::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![ensure_server])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            shutdown_managed(app_handle.state::<ServerManager>().inner());
        }
    });
}
