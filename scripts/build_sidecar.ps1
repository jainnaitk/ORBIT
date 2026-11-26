# scripts/build_sidecar.ps1
# Builds the standalone PyInstaller sidecar binary for ORBIT and stages libmpv-2.dll.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "=== ORBIT Sidecar & libmpv-2 Build Script ===" -ForegroundColor Cyan

# 1. Locate Python executable
$python = $null
if (Test-Path "$Root\.venv\Scripts\python.exe") {
    $python = "$Root\.venv\Scripts\python.exe"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
} else {
    throw "Python was not found in .venv or system PATH."
}

Write-Host "Using Python: $python" -ForegroundColor Gray

# Ensure pyinstaller is available (PS 5.1 turns native stderr into terminating error under Stop)
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)" 2>$null
$pyinstallerExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap

if ($pyinstallerExit -ne 0) {
    Write-Host "PyInstaller not found. Installing via uv / pip..." -ForegroundColor Yellow
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv pip install --python $python pyinstaller
    } else {
        & $python -m pip install pyinstaller
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install PyInstaller into $python"
    }
}

# 2. Check and handle libmpv-2.dll
$localDll = "$Root/bin/libmpv-2.dll"
if (-not (Test-Path $localDll)) {
    Write-Host "bin/libmpv-2.dll not found. Downloading latest 64-bit libmpv-2.dll from shinchiro/mpv-winbuild-cmake..." -ForegroundColor Yellow
    $releasesUrl = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
    $release = Invoke-RestMethod -Uri $releasesUrl -Headers @{ "User-Agent" = "PowerShell" }
    $asset = $release.assets | Where-Object { $_.name -like "mpv-dev-x86_64-*.7z" } | Select-Object -First 1
    if (-not $asset) {
        $asset = $release.assets | Where-Object { $_.name -like "mpv-dev-*.7z" } | Select-Object -First 1
    }
    if (-not $asset) {
        throw "Could not locate mpv-dev 7z release asset from shinchiro/mpv-winbuild-cmake."
    }

    $tempArchive = Join-Path $env:TEMP $asset.name
    Write-Host "Downloading $($asset.browser_download_url)..." -ForegroundColor Gray
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tempArchive

    $extractDir = Join-Path $env:TEMP "mpv_extracted"
    if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

    if (Get-Command 7z -ErrorAction SilentlyContinue) {
        & 7z x $tempArchive -o"$extractDir" -y | Out-Null
    } elseif (Get-Command tar -ErrorAction SilentlyContinue) {
        & tar -xf $tempArchive -C $extractDir
    } else {
        throw "Neither 7z nor tar is available to extract $tempArchive."
    }

    $foundDll = Get-ChildItem -Path $extractDir -Recurse -Filter "libmpv-2.dll" | Select-Object -First 1
    if (-not $foundDll) {
        throw "libmpv-2.dll was not found inside the downloaded mpv package."
    }

    if (-not (Test-Path "$Root/bin")) {
        New-Item -ItemType Directory -Force -Path "$Root/bin" | Out-Null
    }
    Copy-Item -Path $foundDll.FullName -Destination $localDll -Force
    Write-Host "Successfully saved libmpv-2.dll into bin/" -ForegroundColor Green
} else {
    Write-Host "Found local libmpv-2.dll at $localDll" -ForegroundColor Green
}

# 3. Stage libmpv-2.dll and vulkan-1.dll fallback for Tauri bundle resources
$resourcesDir = "$Root/ui/src-tauri/resources"
if (-not (Test-Path $resourcesDir)) {
    New-Item -ItemType Directory -Force -Path $resourcesDir | Out-Null
}
Copy-Item -Path $localDll -Destination "$resourcesDir/libmpv-2.dll" -Force
Write-Host "Staged libmpv-2.dll to $resourcesDir/libmpv-2.dll" -ForegroundColor Green

$localVulkanDll = "$Root/bin/vulkan-1.dll"
if (-not (Test-Path $localVulkanDll)) {
    Write-Host "bin/vulkan-1.dll not found. Downloading Vulkan Loader (win-x64) fallback..." -ForegroundColor Yellow
    $vulkanPkgUrl = "https://api.nuget.org/v3-flatcontainer/silk.net.vulkan.loader.native/2025.9.12/silk.net.vulkan.loader.native.2025.9.12.nupkg"
    $vulkanZip = Join-Path $env:TEMP "silk_vulkan_loader.zip"
    $vulkanExtract = Join-Path $env:TEMP "silk_vulkan_extracted"
    try {
        Invoke-WebRequest -Uri $vulkanPkgUrl -OutFile $vulkanZip
        if (Test-Path $vulkanExtract) { Remove-Item -Recurse -Force $vulkanExtract }
        Expand-Archive -Path $vulkanZip -DestinationPath $vulkanExtract -Force
        $extractedVulkan = Join-Path $vulkanExtract "runtimes\win-x64\native\vulkan-1.dll"
        if (Test-Path $extractedVulkan) {
            Copy-Item -Path $extractedVulkan -Destination $localVulkanDll -Force
        }
    } catch {
        Write-Host "NuGet Vulkan Loader download failed ($($_.Exception.Message)); checking System32..." -ForegroundColor Yellow
    }
    if (-not (Test-Path $localVulkanDll) -and (Test-Path "$env:WINDIR\System32\vulkan-1.dll")) {
        Copy-Item -Path "$env:WINDIR\System32\vulkan-1.dll" -Destination $localVulkanDll -Force
    }
}
if (Test-Path $localVulkanDll) {
    Copy-Item -Path $localVulkanDll -Destination "$resourcesDir/vulkan-1.dll" -Force
    Write-Host "Staged vulkan-1.dll to $resourcesDir/vulkan-1.dll" -ForegroundColor Green
} else {
    throw "vulkan-1.dll could not be staged (required by libmpv-2.dll on systems without GPU Vulkan drivers)."
}

# 4. Build standalone PyInstaller sidecar binary
$binariesDir = "$Root/ui/src-tauri/binaries"
if (-not (Test-Path $binariesDir)) {
    New-Item -ItemType Directory -Force -Path $binariesDir | Out-Null
}

$targetSidecar = "$binariesDir/orbit-backend-x86_64-pc-windows-msvc.exe"
$distDir = "$Root/dist_sidecar"
$workDir = "$Root/build_sidecar"

Write-Host "Building PyInstaller sidecar binary into $targetSidecar..." -ForegroundColor Cyan

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--clean",
    "--noconfirm",
    "--name", "orbit-backend",
    "--onefile",
    "--paths", "$Root/src",
    "--distpath", $distDir,
    "--workpath", $workDir,
    "--collect-all", "uvicorn",
    "--collect-all", "metrolite",
    "--collect-all", "ytmusicapi",
    "--collect-all", "yt_dlp",
    "--collect-all", "mutagen",
    "--hidden-import", "aiosqlite",
    "--hidden-import", "pynput.keyboard._win32",
    "--hidden-import", "pynput.mouse._win32",
    "--exclude-module", "tkinter",
    "--exclude-module", "unittest",
    "--exclude-module", "pytest",
    "--exclude-module", "pydoc",
    "--exclude-module", "test",
    "--exclude-module", "curses",
    "--exclude-module", "idlelib",
    "$Root/src/metrolite/__main__.py"
)

& $python @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$builtExe = "$distDir/orbit-backend.exe"
if (-not (Test-Path $builtExe)) {
    throw "Expected sidecar binary was not found at $builtExe"
}

Copy-Item -Path $builtExe -Destination $targetSidecar -Force
Write-Host "Successfully generated Tauri externalBin: $targetSidecar" -ForegroundColor Green

$releaseDir = "$Root/ui/src-tauri/target/release"
if (Test-Path $releaseDir) {
    Copy-Item -Path $builtExe -Destination "$releaseDir/orbit-backend.exe" -Force
    Write-Host "Updated release folder sidecar: $releaseDir/orbit-backend.exe" -ForegroundColor Green
}

# Clean temporary build folders
if (Test-Path $workDir) { Remove-Item -Recurse -Force $workDir }
if (Test-Path "$Root/orbit-backend.spec") { Remove-Item -Force "$Root/orbit-backend.spec" }

Write-Host "=== Sidecar & libmpv packaging complete ===" -ForegroundColor Green
