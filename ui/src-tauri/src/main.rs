// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{LogicalSize, Manager, PhysicalPosition, PhysicalSize, WebviewWindow};

const ISLAND_PILL_W: f64 = 380.0;
const ISLAND_PILL_H: f64 = 142.0;
const ISLAND_CIRCLE_SIZE: f64 = 78.0;
const ISLAND_TOP_MARGIN: f64 = 20.0;

static LAST_TRIM_MS: AtomicU64 = AtomicU64::new(0);

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

    pub const SWP_NOZORDER: u32 = 0x0004;
    pub const SWP_NOACTIVATE: u32 = 0x0010;
    pub const SWP_FRAMECHANGED: u32 = 0x0020;
    pub const SWP_NOCOPYBITS: u32 = 0x0100;
    pub const SW_SHOWNOACTIVATE: i32 = 4;
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

    let _ = window.set_shadow(false);
    let _ = window.set_min_size(Some(LogicalSize::new(64.0, 64.0)));
    let _ = window.set_resizable(false);
    let _ = window.set_always_on_top(true);

    apply_bounds_atomic(&window, target_x, target_y, phys_w, phys_h);

    spawn_memory_trim();
    Ok(saved)
}

/// Smoothly resizes and horizontally re-centers Dynamic Island (Pill <-> Circle) in place
/// without hiding the window, clamping safely inside the monitor work area.
#[tauri::command]
fn resize_dynamic_island(window: WebviewWindow, expanded: bool) -> Result<(), String> {
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

    let next_w = ((if expanded { ISLAND_PILL_W } else { ISLAND_CIRCLE_SIZE }) * scale).round() as i32;
    let next_h = ((if expanded { ISLAND_PILL_H } else { ISLAND_CIRCLE_SIZE }) * scale).round() as i32;

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

    if !expanded {
        spawn_memory_trim();
    }
    Ok(())
}

/// Restores saved main window geometry while the WebView2 surface is blank transparent
/// (no SW_HIDE / SW_SHOW so WebView2 never freezes or flashes a stale Dynamic Island frame).
#[tauri::command]
fn exit_dynamic_island(
    window: WebviewWindow,
    saved: Option<SavedWindowBounds>,
) -> Result<(), String> {
    disable_window_transitions(&window);
    let _ = window.set_always_on_top(false);
    let _ = window.set_resizable(true);
    let _ = window.set_shadow(false);
    let _ = window.set_min_size(Some(LogicalSize::new(380.0, 320.0)));

    if let Some(bounds) = saved {
        let scale = window.scale_factor().unwrap_or(1.0);
        let min_w = (380.0 * scale).round() as u32;
        let min_h = (320.0 * scale).round() as u32;
        let mut w = bounds.width.max(min_w) as i32;
        let mut h = bounds.height.max(min_h) as i32;
        let mut x = bounds.x;
        let mut y = bounds.y;

        // Clamp restored geometry cleanly inside the active monitor work area
        if let Ok(Some(mon)) = window.current_monitor() {
            let wa = mon.work_area();
            w = w.min(wa.size.width as i32);
            h = h.min(wa.size.height as i32);
            let max_x = wa.position.x + (wa.size.width as i32 - w).max(0);
            let max_y = wa.position.y + (wa.size.height as i32 - h).max(0);
            x = x.clamp(wa.position.x, max_x);
            y = y.clamp(wa.position.y, max_y);
        }

        apply_bounds_atomic(&window, x, y, w, h);

        if bounds.was_maximized {
            let _ = window.maximize();
        }
    } else {
        let _ = window.set_size(LogicalSize::new(1120.0, 700.0));
    }

    Ok(())
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            // Disable DWM hide/show transition ghosting on frameless transparent window
            if let Some(main_win) = app.get_webview_window("main") {
                disable_window_transitions(&main_win);
            }
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
            exit_dynamic_island
        ])
        .run(tauri::generate_context!())
        .expect("error while running metrolite application");
}
