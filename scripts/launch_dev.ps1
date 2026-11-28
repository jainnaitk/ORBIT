$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$UiDir = Join-Path $RepoRoot "ui"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DebugExe = Join-Path $RepoRoot "ui\src-tauri\target\debug\orbit.exe"
$LogDir = Join-Path $env:LOCALAPPDATA "metrolite"

if (Get-Process -Name orbit -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $DebugExe }) {
    Start-Process -FilePath $DebugExe -WorkingDirectory $UiDir
    return
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-HttpOk {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

$backendProc = $null
$viteProc = $null

try {
    # 1. Start live Python backend (updated src/metrolite code) if not already listening
    if (-not (Test-HttpOk "http://127.0.0.1:8766/health")) {
        $pyExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
        $backendOut = Join-Path $LogDir "dev-backend.out.log"
        $backendErr = Join-Path $LogDir "dev-backend.err.log"
        $backendProc = Start-Process -FilePath $pyExe `
            -ArgumentList "-m", "metrolite", "run" `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $backendOut `
            -RedirectStandardError $backendErr `
            -PassThru
    }

    # This launcher owns Vite; building the debug shell must not run Tauri's beforeDevCommand.
    $tauriDir = Join-Path $UiDir 'src-tauri'
    $inputs = Get-Item "$tauriDir\src\*.rs", "$tauriDir\Cargo.toml", "$tauriDir\Cargo.lock", "$tauriDir\tauri.conf.json", "$tauriDir\build.rs"
    $latestInput = ($inputs | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
    if (-not (Test-Path $DebugExe) -or (Get-Item $DebugExe).LastWriteTimeUtc -lt $latestInput) {
        Push-Location $tauriDir
        try {
            & cargo build --locked
            if ($LASTEXITCODE -ne 0) { throw 'Could not build the ORBIT desktop shell.' }
        } finally { Pop-Location }
    }

    if (-not (Test-HttpOk "http://127.0.0.1:1420/")) {
        $viteOut = Join-Path $LogDir "dev-vite.out.log"
        $viteErr = Join-Path $LogDir "dev-vite.err.log"
        $viteProc = Start-Process -FilePath "cmd.exe" `
            -ArgumentList "/c", "npx vite --port 1420 --host 127.0.0.1" `
            -WorkingDirectory $UiDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $viteOut `
            -RedirectStandardError $viteErr `
            -PassThru
    }

    # 3. Wait until backend and Vite dev server are ready (up to 25s)
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        if ((Test-HttpOk "http://127.0.0.1:8766/health") -and (Test-HttpOk "http://127.0.0.1:1420/")) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    $orbitProc = Start-Process -FilePath $DebugExe -WorkingDirectory $UiDir -PassThru

    if ($null -ne $orbitProc) {
        $orbitProc.WaitForExit()
    }
} finally {
    if ($null -ne $viteProc -and -not $viteProc.HasExited) {
        & taskkill /PID $viteProc.Id /T /F 2>$null | Out-Null
    }
    if ($null -ne $backendProc -and -not $backendProc.WaitForExit(8000)) {
        & taskkill /PID $backendProc.Id /T /F 2>$null | Out-Null
    }
}
