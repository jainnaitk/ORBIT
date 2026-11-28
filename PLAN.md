# Follow-up fix plan and verification

| Status | Approved work | Regression coverage |
| --- | --- | --- |
| Complete | F1: keep old controls and EOF callbacks from acting on newer playback | [`tests/test_bridge.py`](tests/test_bridge.py): delayed broadcasts, stream resolution, EOF, pause and history writes |
| Complete | F2: honor queue edits made during radio refill | [`tests/test_bridge.py`](tests/test_bridge.py): Play Next at EOF, multi-skip, and pause during refill |
| Complete | F3: count each qualifying replay separately | [`tests/test_bridge.py`](tests/test_bridge.py): Repeat One above and below threshold, with pause/resume |
| Complete | F4: prevent stale lyric responses from replacing a refresh | [`tests/test_lyrics_phase_b.py`](tests/test_lyrics_phase_b.py): late provider and active file writer, with readback of both sidecars |

## Verify from Windows PowerShell

Run in `C:\ai_work\ORBIT` with the project's Python and UI dependencies installed:

```powershell
Set-Location C:\ai_work\ORBIT
$originalLocalAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Join-Path $env:TEMP ("orbit-check-" + [guid]::NewGuid())
    & .\.venv\Scripts\ruff.exe check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff failed' }
    & .\.venv\Scripts\python.exe -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw 'pytest failed' }
} finally {
    $env:LOCALAPPDATA = $originalLocalAppData
}
npm.cmd --prefix ui run build
```

To run the updated working tree, quit any existing ORBIT instance from the tray, then run:

```powershell
Set-Location C:\ai_work\ORBIT
.\launch.bat
```
