<#
  ORBIT website deploy: Cloudflare Pages project "orbit-music".

  Wrangler resolves functions/ from the CURRENT directory, so this script always
  runs the deploy from inside <repo>\website with "." as the asset directory.
  Deploying from the repo root would silently skip functions/api/download.js.

  Usage (from anywhere in the repo):
    .\scripts\deploy_website.ps1
    .\scripts\deploy_website.ps1 -Branch dev            # preview deployment
    .\scripts\deploy_website.ps1 -SkipVerify
#>
[CmdletBinding()]
param(
  [string]$ProjectName = "orbit-music",
  [string]$Branch = "main",
  [string]$DistDir = ".",
  [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# PS 5.1 turns native stderr into ErrorRecords; with ErrorActionPreference=Stop that
# aborts the script even when the command succeeds. Run native calls leniently.
function Invoke-Native {
  param([string[]]$CommandArgs)
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & npx @CommandArgs 2>&1
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $old
  }
  [pscustomobject]@{ Output = @($out); Code = $code }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$siteDir = Join-Path $repoRoot "website"
$index = Join-Path $siteDir "index.html"
$fn = Join-Path $siteDir "functions\api\download.js"
$wrangler = "wrangler@4"

if (-not (Test-Path $siteDir)) { Fail "website/ not found at $siteDir" }
Set-Location $siteDir

Step "Preflight"
node --version | Out-Host
if (-not (Test-Path $index)) { Fail "website\index.html is missing" }
if (-not (Test-Path $fn)) { Fail "website\functions\api\download.js is missing - /api/download would 404" }
Write-Host ("Site:      {0} ({1:N0} KB)" -f $index, ((Get-Item $index).Length / 1KB))
Write-Host ("Function:  {0}" -f $fn)

$assets = Get-ChildItem $siteDir -Recurse -File -Force |
  Where-Object { $_.FullName -notmatch '\\functions\\' }
$big = $assets | Where-Object { $_.Length -gt 25MB }
if ($big) {
  Write-Host "WARNING: files above the 25 MiB Pages per-asset limit:" -ForegroundColor Yellow
  $big | ForEach-Object { Write-Host ("  {0}  {1:N1} MiB" -f $_.FullName, ($_.Length / 1MB)) }
}

$who = Invoke-Native @("--yes", $wrangler, "whoami")
$who.Output | Select-String -Pattern "logged in|Account ID" | Out-Host
if ($who.Code -ne 0) { Fail "Wrangler is not authenticated. Run: npx $wrangler login" }

Step "Deploying $ProjectName ($Branch) from $siteDir"
$deploy = Invoke-Native @("--yes", $wrangler, "pages", "deploy", $DistDir, "--project-name", $ProjectName, "--branch", $Branch, "--commit-dirty=true")
$output = $deploy.Output
$output | Out-Host
if ($deploy.Code -ne 0) { Fail "Deploy failed" }

if ($output -match 'No functions|functions directory not found') {
  Write-Host "WARNING: Wrangler reported no Functions directory." -ForegroundColor Yellow
}

$prodUrl = "https://$ProjectName.pages.dev"
$deployPattern = 'https://[a-z0-9]+\.' + [regex]::Escape($ProjectName) + '\.pages\.dev'
$deployUrl = ($output | Select-String -Pattern $deployPattern -AllMatches |
  ForEach-Object { $_.Matches.Value } | Select-Object -First 1)

if (-not $SkipVerify) {
  Step "Verifying $prodUrl"
  Start-Sleep -Seconds 6

  $homeCode = & curl.exe -s -o NUL -w "%{http_code}" $prodUrl
  Write-Host ("GET /               -> {0}" -f $homeCode)
  if ($homeCode -ne "200") { Write-Host "  WARNING: homepage did not return 200" -ForegroundColor Yellow }

  $dlHeaders = & curl.exe -sI --max-time 25 "$prodUrl/api/download"
  $dlCode = ($dlHeaders | Select-String -Pattern '^HTTP/' | Select-Object -Last 1).Line
  $dlType = ($dlHeaders | Select-String -Pattern '^content-type:' | Select-Object -Last 1).Line
  $dlDisp = ($dlHeaders | Select-String -Pattern '^content-disposition:' | Select-Object -Last 1).Line
  Write-Host ("HEAD /api/download  -> {0}" -f $dlCode)
  $dlHeaders | Select-String -Pattern 'content-type|content-disposition|cache-control' | ForEach-Object { Write-Host ("  {0}" -f $_.Line.Trim()) }

  if ($dlCode -match ' 200' -and $dlDisp) {
    Write-Host "  Installer route is live." -ForegroundColor Green
  } elseif ($dlCode -match ' 404' -and $dlType -match 'text/plain') {
    Write-Host "  Function is live; expected 404 until a GitHub Release with ORBIT-Setup.exe exists." -ForegroundColor Yellow
  } elseif ($dlCode -match ' 200' -and $dlType -match 'text/html') {
    Write-Host "  Function NOT matched - this is the static index.html fallback." -ForegroundColor Red
    Write-Host "  Deploy from inside website\ with '.', not from the repo root." -ForegroundColor Red
  } elseif ($dlCode -match ' 200') {
    Write-Host "  Route responded 200 but without an attachment header - inspect the Function." -ForegroundColor Yellow
  } else {
    Write-Host "  Unexpected status - inspect the Function and routes." -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "Production: $prodUrl" -ForegroundColor Green
if ($deployUrl) { Write-Host "Deployment: $deployUrl" -ForegroundColor Green }
exit 0