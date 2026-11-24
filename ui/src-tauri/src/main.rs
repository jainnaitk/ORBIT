// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, LogicalSize, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

static SIDECAR_CHILD: Mutex<Option<std::process::Child>> = Mutex::new(None);

const ISLAND_PILL_W: f64 = 380.0;
const ISLAND_PILL_H: f64 = 142.0;
const ISLAND_CIRCLE_SIZE: f64 = 78.0;
const ISLAND_CARD_SIZE: f64 = 236.0;
const ISLAND_TOP_MARGIN: f64 = 20.0;

static LAST_TRIM_MS: AtomicU64 = AtomicU64::new(0);
static EXIT_REQUESTED: AtomicBool = AtomicBool::new(false);
static ISLAND_ACTIVE: AtomicBool = AtomicBool::new(false);
static ISLAND_PINNED: AtomicBool = AtomicBool::new(true);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BridgeConfig {
    pub host: String,
    pub port: u16,
    pub token: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SavedWindowBounds {
    pub width: u32,
    pub height: u32,
    pub x: i32,
    pub y: i32,
    #[serde(rename = "wasMaximized")]
    pub was_maximized: bool,
}

fn get_bridge_path() -> PathBuf {
    if let Ok(local_app_data) = std::env::var("LOCALAPPDATA") {
        PathBuf::from(local_app_data).join("metrolite").join("bridge.json")
    } else if let Ok(home) = std::env::var("USERPROFILE") {
        PathBuf::from(home)
            .join("AppData")
            .join("Local")
            .join("metrolite")
            .join("bridge.json")
    } else {
        PathBuf::from(".localdata").join("metrolite").join("bridge.json")
    }
}

#[cfg(windows)]
mod win_mem {
    use std::collections::{HashMap, VecDeque};
    use std::ffi::c_void;
    use std::mem::size_of;

    const TH32CS_SNAPPROCESS: u32 = 0x0000_0002;
    const PROCESS_SET_QUOTA: u32 = 0x0100;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const INVALID_HANDLE_VALUE: *mut c_void = -1isize as *mut c_void;

    pub const SWP_NOSIZE: u32 = 0x0001;
    pub const SWP_NOMOVE: u32 = 0x0002;
    pub const SWP_NOZORDER: u32 = 0x0004;
    pub const SWP_NOACTIVATE: u32 = 0x0010;
    pub const SWP_FRAMECHANGED: u32 = 0x0020;
    pub const SWP_SHOWWINDOW: u32 = 0x0040;
    pub const SWP_NOCOPYBITS: u32 = 0x0100;
    pub const SW_SHOWNOACTIVATE: i32 = 4;
    const HWND_TOPMOST: *mut c_void = -1isize as *mut c_void;
    const HWND_NOTOPMOST: *mut c_void = -2isize as *mut c_void;
    const DWMWA_TRANSITIONS_FORCEDISABLED: u32 = 3;

    #[link(name = "dwmapi")]
    extern "system" {
        fn DwmSetWindowAttribute(
            hwnd: *mut c_void,
            dw_attribute: u32,
            pv_attribute: *const c_void,
            cb_attribute: u32,
        ) -> i32;
    }

    pub fn disable_dwm_transitions(hwnd: *mut c_void) {
        if hwnd.is_null() {
            return;
        }
        let disable: i32 = 1;
        unsafe {
            let _ = DwmSetWindowAttribute(
                hwnd,
                DWMWA_TRANSITIONS_FORCEDISABLED,
                &disable as *const i32 as *const c_void,
                size_of::<i32>() as u32,
            );
        }
    }

    #[repr(C)]
    struct ProcessEntry32W {
        dw_size: u32,
        cnt_usage: u32,
        th32_process_id: u32,
        th32_default_heap_id: usize,
        th32_module_id: u32,
        cnt_threads: u32,
        th32_parent_process_id: u32,
        pc_pri_base: i32,
        dw_flags: u32,
        sz_exe_file: [u16; 260],
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentProcess() -> *mut c_void;
        fn GetCurrentProcessId() -> u32;
        fn SetProcessWorkingSetSize(
            h_process: *mut c_void,
            dw_min: usize,
            dw_max: usize,
        ) -> i32;
        fn CreateToolhelp32Snapshot(dw_flags: u32, th32_process_id: u32) -> *mut c_void;
        fn Process32FirstW(h_snapshot: *mut c_void, lppe: *mut ProcessEntry32W) -> i32;
        fn Process32NextW(h_snapshot: *mut c_void, lppe: *mut ProcessEntry32W) -> i32;
        fn OpenProcess(
            dw_desired_access: u32,
            b_inherit_handle: i32,
            dw_process_id: u32,
        ) -> *mut c_void;
        fn CloseHandle(h_object: *mut c_void) -> i32;
    }

    #[link(name = "user32")]
    extern "system" {
        fn SetWindowPos(
            hwnd: *mut c_void,
            hwnd_insert_after: *mut c_void,
            x: i32,
            y: i32,
            cx: i32,
            cy: i32,
            u_flags: u32,
        ) -> i32;
        fn ShowWindow(hwnd: *mut c_void, n_cmd_show: i32) -> i32;
    }

    pub fn set_window_bounds_atomic(hwnd: *mut c_void, x: i32, y: i32, w: i32, h: i32) {
        if hwnd.is_null() {
            return;
        }
        unsafe {
            let _ = SetWindowPos(
                hwnd,
                std::ptr::null_mut(),
                x,
                y,
                w,
                h,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOCOPYBITS | SWP_FRAMECHANGED,
            );
        }
    }

    pub fn set_window_bounds_smooth(hwnd: *mut c_void, x: i32, y: i32, w: i32, h: i32) {
        if hwnd.is_null() {
            return;
        }
        unsafe {
            let _ = SetWindowPos(
                hwnd,
                std::ptr::null_mut(),
                x,
                y,
                w,
                h,
                SWP_NOZORDER | SWP_NOACTIVATE,
            );
        }
    }

    pub fn set_window_topmost(hwnd: *mut c_void, topmost: bool) {
        if hwnd.is_null() {
            return;
        }
        unsafe {
            let _ = SetWindowPos(
                hwnd,
                if topmost { HWND_TOPMOST } else { HWND_NOTOPMOST },
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            );
        }
    }

    pub fn set_window_bounds_restore(hwnd: *mut c_void, x: i32, y: i32, w: i32, h: i32) {
        if hwnd.is_null() {
            return;
        }
        unsafe {
            let _ = SetWindowPos(
                hwnd,
                HWND_NOTOPMOST,
                x,
                y,
                w,
                h,
                SWP_SHOWWINDOW,
            );
        }
    }

    pub fn show_window_no_activate(hwnd: *mut c_void) {
        if hwnd.is_null() {
            return;
        }
        unsafe {
            let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        }
    }

    /// Trims working-set pages for metrolite.exe and all descendant msedgewebview2.exe processes.
    pub fn trim_process_tree() {
        unsafe {
            let _ = SetProcessWorkingSetSize(GetCurrentProcess(), usize::MAX, usize::MAX);

            let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if snap.is_null() || snap == INVALID_HANDLE_VALUE {
                return;
            }

            let mut children_map: HashMap<u32, Vec<u32>> = HashMap::new();
            let mut entry: ProcessEntry32W = std::mem::zeroed();
            entry.dw_size = size_of::<ProcessEntry32W>() as u32;

            if Process32FirstW(snap, &mut entry) != 0 {
                loop {
                    children_map
                        .entry(entry.th32_parent_process_id)
                        .or_default()
                        .push(entry.th32_process_id);
                    if Process32NextW(snap, &mut entry) == 0 {
                        break;
                    }
                }
            }
            let _ = CloseHandle(snap);

            let root_pid = GetCurrentProcessId();
            let mut queue = VecDeque::new();
            queue.push_back(root_pid);

            while let Some(pid) = queue.pop_front() {
                if let Some(kids) = children_map.get(&pid) {
                    for &child_pid in kids {
                        queue.push_back(child_pid);
                        let h = OpenProcess(
                            PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION,
                            0,
                            child_pid,
                        );
                        if !h.is_null() {
                            let _ = SetProcessWorkingSetSize(h, usize::MAX, usize::MAX);
                            let _ = CloseHandle(h);
                        }
                    }
                }
            }
        }
    }
}

fn spawn_memory_trim() {
    #[cfg(windows)]
    {
        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        let prev = LAST_TRIM_MS.load(Ordering::Relaxed);
        if now_ms.saturating_sub(prev) < 2000 {
            return;
        }
        LAST_TRIM_MS.store(now_ms, Ordering::Relaxed);
        std::thread::spawn(|| {
            win_mem::trim_process_tree();
        });
    }
}

fn apply_bounds_atomic(window: &WebviewWindow, x: i32, y: i32, w: i32, h: i32) {
    #[cfg(windows)]
    {
        if let Ok(hwnd) = window.hwnd() {
            win_mem::set_window_bounds_atomic(hwnd.0 as *mut std::ffi::c_void, x, y, w, h);
            return;
        }
    }
    let _ = window.set_size(PhysicalSize::new(w.max(1) as u32, h.max(1) as u32));
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

fn apply_bounds_smooth(window: &WebviewWindow, x: i32, y: i32, w: i32, h: i32) {
    #[cfg(windows)]
    {
        if let Ok(hwnd) = window.hwnd() {
            win_mem::set_window_bounds_smooth(hwnd.0 as *mut std::ffi::c_void, x, y, w, h);
            return;
        }
    }
    let _ = window.set_size(PhysicalSize::new(w.max(1) as u32, h.max(1) as u32));
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

fn apply_bounds_restore(window: &WebviewWindow, x: i32, y: i32, w: i32, h: i32) {
    #[cfg(windows)]
    {
        if let Ok(hwnd) = window.hwnd() {
            win_mem::set_window_bounds_restore(hwnd.0 as *mut std::ffi::c_void, x, y, w, h);
            return;
        }
    }
    let _ = window.set_size(PhysicalSize::new(w.max(1) as u32, h.max(1) as u32));
    let _ = window.set_position(PhysicalPosition::new(x, y));
}

fn enforce_topmost(window: &WebviewWindow, topmost: bool) {
    let _ = window.set_always_on_top(topmost);
    #[cfg(windows)]
    {
        if let Ok(hwnd) = window.hwnd() {
            win_mem::set_window_topmost(hwnd.0 as *mut std::ffi::c_void, topmost);
        }
    }
}

#[tauri::command]
fn get_bridge_config() -> Result<BridgeConfig, String> {
    let path = get_bridge_path();
    if !path.exists() {
        return Err(format!("Bridge config not found at {}", path.display()));
    }
    let content = fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read bridge config at {}: {}", path.display(), e))?;
    let config: BridgeConfig = serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse bridge JSON: {}", e))?;
    Ok(config)
}

#[tauri::command]
fn trim_process_memory() {
    spawn_memory_trim();
}

#[tauri::command]
fn show_main_window(window: WebviewWindow, focus: bool) -> Result<(), String> {
    #[cfg(windows)]
    {
        if !focus {
            if let Ok(hwnd) = window.hwnd() {
                win_mem::show_window_no_activate(hwnd.0 as *mut std::ffi::c_void);
                return Ok(());
            }
        }
    }
    window.show().map_err(|e| e.to_string())?;
    if focus {
        let _ = window.set_focus();
    }
    Ok(())
}

fn disable_window_transitions(window: &WebviewWindow) {
    #[cfg(windows)]
    {
        if let Ok(hwnd) = window.hwnd() {
            win_mem::disable_dwm_transitions(hwnd.0 as *mut std::ffi::c_void);
        }
    }
}

/// Atomically captures bounds, shrinks & centers below top work-area edge for Dynamic Island.
/// Window stays visible with a 100% transparent WebView2 frame so WebView2 never freezes rAF
/// or flashes stale swap-chain frames on Windows 11 DWM.
#[tauri::command]
fn enter_dynamic_island(window: WebviewWindow) -> Result<SavedWindowBounds, String> {
    disable_window_transitions(&window);
    let was_maximized = window.is_maximized().unwrap_or(false);
    let size = window.inner_size().map_err(|e| e.to_string())?;
    let pos = window.outer_position().map_err(|e| e.to_string())?;

    if was_maximized {
        let _ = window.unmaximize();
    }

    let saved = SavedWindowBounds {
        width: size.width,
        height: size.height,
        x: pos.x,
        y: pos.y,
        was_maximized,
    };

    let scale = window.scale_factor().unwrap_or(1.0);
    let phys_w = (ISLAND_PILL_W * scale).round() as i32;
    let phys_h = (ISLAND_PILL_H * scale).round() as i32;
    let top_margin = (ISLAND_TOP_MARGIN * scale).round() as i32;

    let (target_x, target_y) = if let Ok(Some(mon)) = window.current_monitor() {
        let wa = mon.work_area();
        let x = wa.position.x + ((wa.size.width as i32 - phys_w) / 2);
        let y = wa.position.y + top_margin;
        (x, y)
    } else {
        (pos.x.max(0), top_margin)
    };

    ISLAND_ACTIVE.store(true, Ordering::SeqCst);
    let pinned = ISLAND_PINNED.load(Ordering::SeqCst);
    let _ = window.set_shadow(false);
    let _ = window.set_min_size(Some(LogicalSize::new(64.0, 64.0)));
    let _ = window.set_resizable(false);
    let _ = window.set_skip_taskbar(pinned);
    enforce_topmost(&window, pinned);

    apply_bounds_atomic(&window, target_x, target_y, phys_w, phys_h);
    if pinned {
        enforce_topmost(&window, true);
    }

    spawn_memory_trim();
    Ok(saved)
}

/// Pin or unpin Dynamic Island on top of all windows while keeping it freely movable anywhere.
#[tauri::command]
fn set_island_pinned(window: WebviewWindow, pinned: bool) -> Result<(), String> {
    ISLAND_PINNED.store(pinned, Ordering::SeqCst);
    if !ISLAND_ACTIVE.load(Ordering::SeqCst) {
        return Ok(());
    }
    let _ = window.set_skip_taskbar(pinned);
    enforce_topmost(&window, pinned);
    Ok(())
}

/// Smoothly resizes and horizontally re-centers Dynamic Island (Pill <-> Circle <-> Art-Card)
/// in place without hiding the window, clamping safely inside the monitor work area.
#[tauri::command]
fn resize_dynamic_island(
    window: WebviewWindow,
    expanded: Option<bool>,
    mode: Option<String>,
) -> Result<(), String> {
    let scale = window.scale_factor().unwrap_or(1.0);
    let cur_pos = window
        .outer_position()
        .unwrap_or(PhysicalPosition::new(0, (ISLAND_TOP_MARGIN * scale).round() as i32));
    let cur_size = window
        .outer_size()
        .unwrap_or(PhysicalSize::new(
            (ISLAND_PILL_W * scale).round() as u32,
            (ISLAND_PILL_H * scale).round() as u32,
        ));

    let resolved_mode = match mode.as_deref() {
        Some("card") => "card",
        Some("circle") => "circle",
        Some("pill") => "pill",
        _ => {
            if expanded.unwrap_or(true) {
                "pill"
            } else {
                "circle"
            }
        }
    };

    let (logical_w, logical_h) = match resolved_mode {
        "card" => (ISLAND_CARD_SIZE, ISLAND_CARD_SIZE),
        "circle" => (ISLAND_CIRCLE_SIZE, ISLAND_CIRCLE_SIZE),
        _ => (ISLAND_PILL_W, ISLAND_PILL_H),
    };

    let next_w = (logical_w * scale).round() as i32;
    let next_h = (logical_h * scale).round() as i32;

    let center_x = cur_pos.x + (cur_size.width as i32) / 2;
    let mut next_x = center_x - next_w / 2;
    let mut next_y = cur_pos.y;

    if let Ok(Some(mon)) = window.current_monitor() {
        let wa = mon.work_area();
        let pad = (12.0 * scale).round() as i32;
        let min_x = wa.position.x + pad;
        let max_x = wa.position.x + wa.size.width as i32 - next_w - pad;
        let min_y = wa.position.y + pad;
        let max_y = wa.position.y + wa.size.height as i32 - next_h - pad;
        if max_x >= min_x {
            next_x = next_x.clamp(min_x, max_x);
        }
        if max_y >= min_y {
            next_y = next_y.clamp(min_y, max_y);
        }
    }

    let _ = window.set_shadow(false);
    apply_bounds_smooth(&window, next_x, next_y, next_w, next_h);
    if ISLAND_PINNED.load(Ordering::SeqCst) {
        enforce_topmost(&window, true);
    }

    if resolved_mode == "circle" {
        spawn_memory_trim();
    }
    Ok(())
}

/// Restores saved main window geometry cleanly in a single non-flashing step and focuses immediately.
#[tauri::command]
fn exit_dynamic_island(
    window: WebviewWindow,
    saved: Option<SavedWindowBounds>,
) -> Result<(), String> {
    ISLAND_ACTIVE.store(false, Ordering::SeqCst);
    disable_window_transitions(&window);
    let _ = window.set_skip_taskbar(false);
    let _ = window.set_always_on_top(false);
    let _ = window.set_resizable(true);
    let _ = window.set_shadow(false);
    let _ = window.set_min_size(Some(LogicalSize::new(380.0, 320.0)));

    if let Some(bounds) = saved {
        if bounds.was_maximized {
            let _ = window.maximize();
            let _ = window.set_focus();
            return Ok(());
        } else {
            let scale = window.scale_factor().unwrap_or(1.0);
            let min_w = (380.0 * scale).round() as u32;
            let min_h = (320.0 * scale).round() as u32;
            let mut w = bounds.width.max(min_w) as i32;
            let mut h = bounds.height.max(min_h) as i32;
            let mut x = bounds.x;
            let mut y = bounds.y;

            if let Ok(Some(mon)) = window.current_monitor() {
                let wa = mon.work_area();
                w = w.min(wa.size.width as i32);
                h = h.min(wa.size.height as i32);
                let max_x = wa.position.x + (wa.size.width as i32 - w).max(0);
                let max_y = wa.position.y + (wa.size.height as i32 - h).max(0);
                x = x.clamp(wa.position.x, max_x);
                y = y.clamp(wa.position.y, max_y);
            }

            apply_bounds_restore(&window, x, y, w, h);
            let _ = window.set_focus();
            return Ok(());
        }
    } else {
        let _ = window.set_size(LogicalSize::new(1120.0, 700.0));
    }

    let _ = window.show();
    let _ = window.set_focus();
    Ok(())
}

const LOW_MEM_BROWSER_ARGS: &str = "--disable-features=msWebOOUI,Translate,SpareRendererForSitePerProcess,msEdgeShopping,msEdgeSidebar,msEdgeCollections,InterestFeedContentSuggestions,AudioServiceOutOfProcess --disable-background-networking --disable-component-update --disable-extensions --disable-sync --disable-3d-apis --disk-cache-size=4194304 --media-cache-size=4194304 --renderer-process-limit=1 --js-flags=--max-old-space-size=64";

fn should_close_to_tray() -> bool {
    let paths = [
        PathBuf::from("config.yaml"),
        if let Ok(local_app_data) = std::env::var("LOCALAPPDATA") {
            PathBuf::from(local_app_data).join("metrolite").join("config.yaml")
        } else {
            PathBuf::from("config.yaml")
        },
    ];

    for path in &paths {
        if path.exists() {
            if let Ok(content) = fs::read_to_string(path) {
                for line in content.lines() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("close_to_tray:") {
                        let parts: Vec<&str> = trimmed.split(':').collect();
                        if parts.len() > 1 {
                            let val = parts[1].trim().to_lowercase();
                            if val.starts_with("false") {
                                return false;
                            }
                        }
                    }
                }
            }
        }
    }
    true
}

fn send_bridge_request(path: &str, method: &str) {
    let bridge_cfg = match get_bridge_config() {
        Ok(cfg) => cfg,
        Err(e) => {
            eprintln!("Failed to get bridge config for tray action {path}: {e}");
            return;
        }
    };

    let url = format!("http://{}:{}{}", bridge_cfg.host, bridge_cfg.port, path);
    let token = bridge_cfg.token;
    let method = method.to_string();

    tauri::async_runtime::spawn(async move {
        let client = reqwest::Client::new();
        let req = match method.as_str() {
            "POST" => client.post(&url),
            "GET" => client.get(&url),
            _ => client.post(&url),
        };
        let _ = req.header("X-Bridge-Token", token).send().await;
    });
}

#[cfg(windows)]
mod win_job {
    use std::ffi::c_void;
    use std::mem::size_of;

    const JOB_OBJECT_LIMIT_BREAKAWAY_OK: u32 = 0x0000_0800;
    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x0000_2000;

    #[repr(C)]
    struct IoCounters {
        read_operation_count: u64,
        write_operation_count: u64,
        other_operation_count: u64,
        read_transfer_count: u64,
        write_transfer_count: u64,
        other_transfer_count: u64,
    }

    #[repr(C)]
    struct JobobjectBasicLimitInformation {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: u32,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    struct JobobjectExtendedLimitInformation {
        basic_limit_information: JobobjectBasicLimitInformation,
        io_info: IoCounters,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(lp_job_attributes: *mut c_void, lp_name: *const u16) -> *mut c_void;
        fn SetInformationJobObject(
            h_job: *mut c_void,
            job_object_info_class: i32,
            lp_job_object_info: *const c_void,
            cb_job_object_info_length: u32,
        ) -> i32;
        fn AssignProcessToJobObject(h_job: *mut c_void, h_process: *mut c_void) -> i32;
    }

    pub struct WindowsJob {
        handle: *mut c_void,
    }

    unsafe impl Send for WindowsJob {}
    unsafe impl Sync for WindowsJob {}

    impl WindowsJob {
        pub fn create() -> Option<Self> {
            unsafe {
                let h_job = CreateJobObjectW(std::ptr::null_mut(), std::ptr::null());
                if h_job.is_null() {
                    return None;
                }
                let mut info: JobobjectExtendedLimitInformation = std::mem::zeroed();
                info.basic_limit_information.limit_flags =
                    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
                let res = SetInformationJobObject(
                    h_job,
                    9, // JobObjectExtendedLimitInformation
                    &info as *const _ as *const c_void,
                    size_of::<JobobjectExtendedLimitInformation>() as u32,
                );
                if res == 0 {
                    return None;
                }
                Some(WindowsJob { handle: h_job })
            }
        }

        pub fn assign(&self, handle: *mut c_void) {
            if !self.handle.is_null() && !handle.is_null() {
                unsafe {
                    let _ = AssignProcessToJobObject(self.handle, handle);
                }
            }
        }
    }
}

#[cfg(windows)]
static WINDOWS_JOB: std::sync::OnceLock<Option<win_job::WindowsJob>> = std::sync::OnceLock::new();

fn find_sidecar_binary() -> Option<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(exe_dir) = current_exe.parent() {
            candidates.push(exe_dir.join("orbit-backend.exe"));
            candidates.push(exe_dir.join("binaries").join("orbit-backend.exe"));
            candidates.push(exe_dir.join("orbit-backend-x86_64-pc-windows-msvc.exe"));
            candidates.push(exe_dir.join("binaries").join("orbit-backend-x86_64-pc-windows-msvc.exe"));
        }
    }

    candidates.push(PathBuf::from("binaries/orbit-backend-x86_64-pc-windows-msvc.exe"));
    candidates.push(PathBuf::from("ui/src-tauri/binaries/orbit-backend-x86_64-pc-windows-msvc.exe"));
    candidates.push(PathBuf::from("../ui/src-tauri/binaries/orbit-backend-x86_64-pc-windows-msvc.exe"));
    candidates.push(PathBuf::from("orbit-backend.exe"));

    for candidate in candidates {
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn is_bridge_healthy() -> bool {
    let bridge_cfg = match get_bridge_config() {
        Ok(cfg) => cfg,
        Err(_) => return false,
    };
    use std::io::{Read, Write};
    use std::net::{SocketAddr, TcpStream};

    let addr = match format!("{}:{}", bridge_cfg.host, bridge_cfg.port).parse::<SocketAddr>() {
        Ok(a) => a,
        Err(_) => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(400)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let req = format!(
        "GET /health HTTP/1.1\r\nHost: {}:{}\r\nConnection: close\r\n\r\n",
        bridge_cfg.host, bridge_cfg.port
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 64];
    if let Ok(n) = stream.read(&mut buf) {
        if n > 12 {
            let resp = String::from_utf8_lossy(&buf[..n]);
            return resp.starts_with("HTTP/1.1 200") || resp.starts_with("HTTP/1.0 200");
        }
    }
    false
}

fn remove_stale_bridge_file() {
    let path = get_bridge_path();
    if path.exists() {
        let _ = fs::remove_file(path);
    }
}

fn spawn_sidecar_if_needed() {
    if is_bridge_healthy() {
        eprintln!("Bridge server is already running and healthy.");
        return;
    }

    remove_stale_bridge_file();

    let sidecar_path = match find_sidecar_binary() {
        Some(path) => path,
        None => {
            eprintln!("Sidecar binary not found. Dev mode or existing bridge expected.");
            return;
        }
    };

    eprintln!("Spawning sidecar: {}", sidecar_path.display());

    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;

        let mut cmd = std::process::Command::new(&sidecar_path);
        cmd.arg("run");
        cmd.creation_flags(CREATE_NO_WINDOW);

        match cmd.spawn() {
            Ok(child) => {
                let job = WINDOWS_JOB.get_or_init(|| win_job::WindowsJob::create());
                if let Some(j) = job.as_ref() {
                    j.assign(child.as_raw_handle() as *mut std::ffi::c_void);
                }
                if let Ok(mut lock) = SIDECAR_CHILD.lock() {
                    *lock = Some(child);
                }
            }
            Err(e) => {
                eprintln!("Failed to spawn sidecar binary: {e}");
            }
        }
    }

    #[cfg(not(windows))]
    {
        let mut cmd = std::process::Command::new(&sidecar_path);
        cmd.arg("run");
        if let Ok(child) = cmd.spawn() {
            if let Ok(mut lock) = SIDECAR_CHILD.lock() {
                *lock = Some(child);
            }
        }
    }
}

fn wait_for_bridge_ready(timeout_duration: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout_duration {
        if is_bridge_healthy() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(80));
    }
    false
}

fn terminate_sidecar() {
    if let Ok(mut lock) = SIDECAR_CHILD.lock() {
        if let Some(mut child) = lock.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn quit_application(app: &AppHandle) {
    EXIT_REQUESTED.store(true, Ordering::SeqCst);

    let bridge_cfg = get_bridge_config().ok();
    let app_handle = app.clone();

    std::thread::spawn(move || {
        if let Some(cfg) = bridge_cfg {
            let client = reqwest::Client::new();
            let url = format!("http://{}:{}/shutdown", cfg.host, cfg.port);
            tauri::async_runtime::block_on(async {
                let _ = client
                    .post(&url)
                    .header("X-Bridge-Token", cfg.token)
                    .timeout(Duration::from_millis(600))
                    .send()
                    .await;
            });
        }
        std::thread::sleep(Duration::from_millis(150));
        terminate_sidecar();
        app_handle.exit(0);
    });
}

/// Launches a downloaded Windows update installer outside the sidecar Job Object
/// and cleanly shuts down ORBIT + sidecar so NSIS never hits a running-process lock
/// or gets terminated when the Job Object closes.
#[tauri::command]
fn launch_update_installer(app: AppHandle, installer_path: String) -> Result<(), String> {
    let updates_dir = get_bridge_path()
        .parent()
        .map(|p| p.join("updates"))
        .ok_or_else(|| "Could not resolve ORBIT updates directory".to_string())?;

    let canon_updates = fs::canonicalize(&updates_dir)
        .map_err(|e| format!("Updates directory unavailable: {e}"))?;
    let canon_target = fs::canonicalize(&installer_path)
        .map_err(|e| format!("Installer file not found: {e}"))?;

    if !canon_target.is_file() || !canon_target.starts_with(&canon_updates) {
        return Err("Installer path is outside ORBIT updates directory".to_string());
    }

    let ext = canon_target
        .extension()
        .and_then(|e| e.to_str())
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_default();
    if ext != "exe" && ext != "msi" {
        return Err(format!("Unsupported installer format: .{ext}"));
    }

    // Strip Win32 extended-length prefix (\\?\) so NSIS / msiexec accept the path cleanly
    let launch_path = canon_target
        .to_string_lossy()
        .strip_prefix(r"\\?\")
        .map(PathBuf::from)
        .unwrap_or(canon_target);

    EXIT_REQUESTED.store(true, Ordering::SeqCst);
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
    }

    let bridge_cfg = get_bridge_config().ok();
    let app_handle = app.clone();

    std::thread::spawn(move || {
        // 1. Gracefully stop backend & sidecar BEFORE launching NSIS so zero files are locked
        if let Some(cfg) = bridge_cfg {
            let client = reqwest::Client::new();
            let url = format!("http://{}:{}/shutdown", cfg.host, cfg.port);
            tauri::async_runtime::block_on(async {
                let _ = client
                    .post(&url)
                    .header("X-Bridge-Token", cfg.token)
                    .timeout(Duration::from_millis(500))
                    .send()
                    .await;
            });
        }
        std::thread::sleep(Duration::from_millis(180));
        terminate_sidecar();

        // 2. Spawn installer completely detached from orbit.exe (outside WINDOWS_JOB)
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;

            let spawned = if ext == "msi" {
                std::process::Command::new("msiexec")
                    .arg("/i")
                    .arg(&launch_path)
                    .creation_flags(CREATE_NEW_PROCESS_GROUP)
                    .spawn()
            } else {
                std::process::Command::new(&launch_path)
                    .creation_flags(CREATE_NEW_PROCESS_GROUP)
                    .spawn()
            };

            if spawned.is_err() {
                // Fallback via Windows shell if elevation or shell dispatch is required
                let _ = std::process::Command::new("cmd")
                    .args(["/C", "start", ""])
                    .arg(&launch_path)
                    .creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
                    .spawn();
            }
        }

        // 3. Exit orbit.exe immediately so NSIS finds zero running ORBIT processes
        app_handle.exit(0);
    });

    Ok(())
}

fn attach_window_close_handler(window: &WebviewWindow) {
    let app = window.app_handle().clone();
    let win_clone = window.clone();
    window.on_window_event(move |event| {
        match event {
            tauri::WindowEvent::CloseRequested { .. } => {
                if !should_close_to_tray() {
                    quit_application(&app);
                }
            }
            tauri::WindowEvent::Focused(false) => {
                // Keep pinned Dynamic Island fixed at top across Alt+Tab / app switching
                if ISLAND_ACTIVE.load(Ordering::SeqCst) && ISLAND_PINNED.load(Ordering::SeqCst) {
                    if win_clone.is_minimized().unwrap_or(false) {
                        let _ = win_clone.unminimize();
                    }
                    enforce_topmost(&win_clone, true);
                }
            }
            _ => {}
        }
    });
}

pub fn ensure_main_window(app: &AppHandle) {
    if let Some(main_win) = app.get_webview_window("main") {
        let _ = main_win.unminimize();
        let _ = main_win.show();
        let _ = main_win.set_focus();
        return;
    }

    ISLAND_ACTIVE.store(false, Ordering::SeqCst);

    let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
        .title("ORBIT")
        .inner_size(1120.0, 700.0)
        .min_inner_size(380.0, 320.0)
        .resizable(true)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .additional_browser_args(LOW_MEM_BROWSER_ARGS);

    match builder.build() {
        Ok(win) => {
            disable_window_transitions(&win);
            attach_window_close_handler(&win);
            let _ = win.show();
            let _ = win.set_focus();
        }
        Err(e) => {
            eprintln!("Failed to recreate main window: {e}");
        }
    }
}

fn start_tray_tooltip_sync(app: AppHandle) {
    std::thread::spawn(move || {
        let client = reqwest::Client::new();
        while !EXIT_REQUESTED.load(Ordering::Relaxed) {
            std::thread::sleep(std::time::Duration::from_millis(1500));
            if EXIT_REQUESTED.load(Ordering::Relaxed) {
                break;
            }
            if let Ok(cfg) = get_bridge_config() {
                let url = format!("http://{}:{}/health", cfg.host, cfg.port);
                let app_clone = app.clone();
                let client_ref = &client;
                tauri::async_runtime::block_on(async move {
                    if let Ok(res) = client_ref
                        .get(&url)
                        .timeout(std::time::Duration::from_millis(800))
                        .send()
                        .await
                    {
                        if let Ok(json) = res.json::<serde_json::Value>().await {
                            let tooltip = if let Some(track) = json.get("track").and_then(|t| t.as_str()) {
                                if !track.trim().is_empty() {
                                    format!("ORBIT • {}", track.trim())
                                } else {
                                    "ORBIT".to_string()
                                }
                            } else {
                                "ORBIT".to_string()
                            };

                            if let Some(tray) = app_clone.tray_by_id("orbit_tray") {
                                let _ = tray.set_tooltip(Some(tooltip));
                            }
                        }
                    }
                });
            }
        }
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            ensure_main_window(app);
        }))
        .setup(|app| {
            // Spawn sidecar silently if not already running
            spawn_sidecar_if_needed();

            // Wait gracefully for bridge to be ready before surfacing UI
            let _ = wait_for_bridge_ready(Duration::from_secs(12));

            // Surface main window once backend is ready
            if let Some(main_win) = app.get_webview_window("main") {
                disable_window_transitions(&main_win);
                attach_window_close_handler(&main_win);
                let _ = main_win.show();
                let _ = main_win.set_focus();
            }

            // Create Windows System Tray
            let open_i = MenuItem::with_id(app, "open", "Open ORBIT", true, None::<&str>)?;
            let play_pause_i = MenuItem::with_id(app, "play_pause", "Play / Pause", true, None::<&str>)?;
            let next_i = MenuItem::with_id(app, "next", "Next Track", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&open_i, &play_pause_i, &next_i, &quit_i])?;

            let tray_icon = app
                .default_window_icon()
                .cloned()
                .or_else(|| tauri::image::Image::from_bytes(include_bytes!("../icons/icon.ico")).ok())
                .expect("failed to load app icon for tray");

            let _tray = TrayIconBuilder::with_id("orbit_tray")
                .icon(tray_icon)
                .tooltip("ORBIT")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "open" => {
                            ensure_main_window(app);
                        }
                        "play_pause" => {
                            send_bridge_request("/player/toggle", "POST");
                        }
                        "next" => {
                            send_bridge_request("/player/next", "POST");
                        }
                        "quit" => {
                            quit_application(app);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    match event {
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        }
                        | TrayIconEvent::DoubleClick {
                            button: MouseButton::Left,
                            ..
                        } => {
                            let app = tray.app_handle();
                            ensure_main_window(app);
                        }
                        _ => {}
                    }
                })
                .build(app)?;

            // Start live tray tooltip sync
            start_tray_tooltip_sync(app.handle().clone());

            // Trim initial cold startup pages once WebView2 settles (~6s after launch)
            std::thread::spawn(|| {
                std::thread::sleep(std::time::Duration::from_secs(6));
                spawn_memory_trim();
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_bridge_config,
            trim_process_memory,
            show_main_window,
            enter_dynamic_island,
            resize_dynamic_island,
            set_island_pinned,
            exit_dynamic_island,
            launch_update_installer
        ])
        .build(tauri::generate_context!())
        .expect("error while building metrolite application")
        .run(|_app_handle, event| {
            match event {
                tauri::RunEvent::WindowEvent {
                    label,
                    event: tauri::WindowEvent::Destroyed,
                    ..
                } => {
                    if label == "main" {
                        ISLAND_ACTIVE.store(false, Ordering::SeqCst);
                        spawn_memory_trim();
                    }
                }
                tauri::RunEvent::ExitRequested { api, .. } => {
                    if should_close_to_tray() && !EXIT_REQUESTED.load(Ordering::SeqCst) {
                        api.prevent_exit();
                        spawn_memory_trim();
                    }
                }
                tauri::RunEvent::Exit => {
                    terminate_sidecar();
                }
                _ => {}
            }
        });
}
