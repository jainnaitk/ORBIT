"""Measure live RAM (Working Set / RSS) of metrolite Python + Tauri/WebView2 processes."""

import os

import psutil


def main() -> None:
    current_pid = os.getpid()

    # Track components
    backend_procs = []
    tauri_procs = []
    webview_procs = []
    dev_procs = []

    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "ppid"]):
        try:
            pid = proc.info["pid"]
            if pid == current_pid:
                continue

            name = (proc.info["name"] or "").lower()
            cmd_list = proc.info["cmdline"] or []
            cmd = " ".join(cmd_list).lower()

            if "measure_ram" in cmd or "music_worker" in cmd:
                continue

            # Ignore powershell / cmd / bash shells running commands
            if name in ["powershell.exe", "cmd.exe", "bash.exe"]:
                continue

            # Check if it's the backend daemon
            if (
                ("-m metrolite" in cmd or "metrolite run" in cmd or "orbit-backend" in cmd or "orbit-backend" in name)
                and ("python" in name or "uv" in name or "orbit-backend" in name)
            ):
                backend_procs.append(proc)
            elif name in ["metrolite.exe", "orbit.exe"]:
                tauri_procs.append(proc)
            elif "msedgewebview2" in name and any(
                "metrolite" in arg.lower() or "orbit" in arg.lower() or "1420" in arg.lower() for arg in cmd_list
            ):
                webview_procs.append(proc)
            elif name in ["node.exe", "esbuild.exe"] and any("vite" in arg.lower() or "1420" in arg.lower() or "metrolite" in arg.lower() for arg in cmd_list):
                dev_procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    def print_group(title: str, procs: list) -> float:
        print(f"\n[{title}]")
        subtotal = 0.0
        for p in procs:
            try:
                mb = p.info["memory_info"].rss / (1024 * 1024)
                subtotal += mb
                cmd_summary = " ".join(p.info["cmdline"] or [])[:55]
                print(f"  PID {p.info['pid']:6d} | {p.info['name']:20s} | {mb:6.1f} MB | {cmd_summary}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        print(f"  Subtotal: {subtotal:.1f} MB")
        return subtotal

    print("=" * 70)
    print("METROLITE LIVE RAM USAGE MEASUREMENT")
    print("=" * 70)

    tauri_total = print_group("Tauri Client (metrolite.exe)", tauri_procs)
    webview_total = print_group("WebView2 Engine (msedgewebview2.exe)", webview_procs)
    backend_total = print_group("Backend Daemon & Audio Engine (Python/libmpv)", backend_procs)

    runtime_total = tauri_total + webview_total + backend_total

    print("\n" + "=" * 70)
    print(f"TOTAL RUNTIME RAM (Tauri + WebView2 + Python/libmpv): {runtime_total:6.1f} MB")
    print("HARD BUDGET (Active Window + Streaming):               185.0 MB")
    delta = 185.0 - runtime_total
    if runtime_total <= 185.0:
        print(f"RESULT: PASSED (Under budget by {delta:.1f} MB)")
    else:
        print(f"RESULT: OVER BUDGET by {-delta:.1f} MB")
    print("=" * 70)

    if dev_procs:
        dev_total = print_group("Vite Dev Server (Dev Only, Not in Prod .exe)", dev_procs)
        print(f"\nNote: Vite dev server processes add {dev_total:.1f} MB in dev mode,")
        print("      which will be 0 MB in production release builds.")


if __name__ == "__main__":
    main()
