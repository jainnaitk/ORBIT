import { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import "./tokens.css";
import "./index.css";

function isTrayPopupWindow(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const internals = (window as unknown as {
      __TAURI_INTERNALS__?: { metadata?: { currentWindow?: { label?: string } } };
    }).__TAURI_INTERNALS__;
    if (internals?.metadata?.currentWindow?.label === "tray_popup") {
      return true;
    }
  } catch {
    // Ignore outside Tauri
  }
  return new URLSearchParams(window.location.search).get("view") === "tray";
}

const LazyApp = lazy(() => import("./App"));
const LazyTrayPopup = lazy(() =>
  import("./components/TrayPopup").then((m) => ({ default: m.TrayPopup }))
);

const isTray = isTrayPopupWindow();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <Suspense fallback={null}>{isTray ? <LazyTrayPopup /> : <LazyApp />}</Suspense>
);
