"""Measure live RAM (Working Set / RSS) of metrolite Python + Tauri/WebView2 processes."""

import psutil


def main() -> None:
    total_mb = 0.0
    found = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info["cmdline"] or []).lower()
            is_backend = "metrolite" in cmd
            is_ui = "metrolite" in name or ("msedgewebview2" in name and "metrolite" in cmd)
            if is_backend or is_ui:
                mb = proc.info["memory_info"].rss / (1024 * 1024)
                total_mb += mb
                found += 1
                print(f"PID {proc.info['pid']:6d} | {proc.info['name']:22s} | {mb:6.1f} MB")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    print("-" * 52)
    print(f"Matched processes: {found} | Total RAM: {total_mb:.1f} MB")
    if total_mb > 200:
        print("WARNING: Over 200 MB target!")


if __name__ == "__main__":
    main()
