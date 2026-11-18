// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BridgeConfig {
    pub host: String,
    pub port: u16,
    pub token: String,
}

fn get_bridge_path() -> PathBuf {
    if let Ok(local_app_data) = std::env::var("LOCALAPPDATA") {
        PathBuf::from(local_app_data).join("metrolite").join("bridge.json")
    } else if let Ok(home) = std::env::var("USERPROFILE") {
        PathBuf::from(home).join("AppData").join("Local").join("metrolite").join("bridge.json")
    } else {
        PathBuf::from(".localdata").join("metrolite").join("bridge.json")
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

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_bridge_config])
        .run(tauri::generate_context!())
        .expect("error while running metrolite application");
}
