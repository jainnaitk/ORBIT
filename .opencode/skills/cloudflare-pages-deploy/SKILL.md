---
name: cloudflare-pages-deploy
description: Deploy the ORBIT website (website/index.html + website/functions/api/download.js) to Cloudflare Pages project orbit-music, verify the site and the /api/download installer route, and manage preview/production deploys. Use for any ORBIT website deploy, redeploy, rollback, or broken /api/download task.
---

# ORBIT website — Cloudflare Pages deployment

Project: `orbit-music` · Production URL: `https://orbit-music.pages.dev`
Repo: `C:\ai_work\ORBIT` · Site root: `C:\ai_work\ORBIT\website`

## ORBIT rule (read first)

Wrangler resolves `functions/` **relative to the current working directory**. The ORBIT
Function lives at `website\functions\api\download.js`, so:

```powershell
cd C:\ai_work\ORBIT\website
npx wrangler@4 pages deploy . --project-name orbit-music --branch main
```

- Deploy `.` from **inside `website`**.
- Deploying `C:\ai_work\ORBIT` (repo root) or `website` from the root **silently skips**
  `functions/` and breaks `/api/download`.
- Never drag-and-drop `website\`: that also drops Functions.
- Do not deploy the repo root: it uploads `.venv`, `src`, `ui`, and `.git`.

Helper (does the cd, validation, deploy and verification):

```powershell
.\scripts\deploy_website.ps1
```

## Pre-deploy checks

```powershell
python scripts\build_website.py          # content guard (currently fails on three.min.js — see below)
Test-Path website\index.html             # must be True
Test-Path website\functions\api\download.js
npx wrangler@4 whoami                    # machine-wide OAuth is already configured
```

Known issue: `website/index.html` line ~45 loads
`https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js`, which
`build_website.py` forbids (`three.min.js`, "No CDN" rule). Fix the page (bundle or remove)
before claiming the site passes the repo's own check. Do not delete the guard to make it pass.

## Deploy

```powershell
cd C:\ai_work\ORBIT\website

# production
npx wrangler@4 pages deploy . --project-name orbit-music --branch main

# preview
npx wrangler@4 pages deploy . --project-name orbit-music --branch dev
```

- First deploy creates the `orbit-music` project (accept the prompt; non-interactive CI must
  create it first with `npx wrangler@4 pages project create orbit-music --production-branch main`).
- `--branch main` → production; any other branch → preview URL.
- 25 MiB per asset; `website/index.html` is ~236 KB, fine.

Local run with Functions and static files together:

```powershell
cd C:\ai_work\ORBIT\website
npx wrangler@4 pages dev .
```

## The download Function

`website/functions/api/download.js` exports `onRequestGet`:

- `/api/download` → streams
  `https://github.com/jainnaitk/ORBIT/releases/latest/download/ORBIT-Setup.exe`
- `/api/download?asset=<name>.exe` → allowlisted `.exe` name only
- GitHub missing/failed → `404` "Latest ORBIT Windows installer not found..."
- Success → `200`, `content-type: application/octet-stream`,
  `content-disposition: attachment; filename="ORBIT-Setup.exe"`, `cache-control: no-store`

Expected timeline: `404` until the first GitHub Release with `ORBIT-Setup.exe` exists,
then `200` without redeploying the site (git integration / latest redirect is live).

## Verify after deploy

```powershell
# homepage
curl.exe -s -o NUL -w "GET / -> %{http_code}`n" https://orbit-music.pages.dev

# installer route (HEAD avoids downloading the exe)
curl.exe -sI https://orbit-music.pages.dev/api/download | Select-String "HTTP/|content-type|content-disposition|cache-control"
```

Pass criteria:

- `GET /` → `200`
- `/api/download` → `404` with `content-type: text/plain` before v1.0.0 is published
  (expected, not a deploy failure)
- after v1.0.0 → `200` + `content-disposition: attachment; filename="ORBIT-Setup.exe"`
- **Trap:** `200` + `content-type: text/html` means the Function was not matched and Pages
  served the static `index.html` fallback. That happens when you deploy from the repo root
  instead of `website\`; it is not a working installer route.

Do not claim success from a deploy log alone; check the live status codes.

## CI/CD

`.github/workflows/deploy-website.yml` deploys `website/` on pushes to `main` that touch
`website/**`. It needs repository secrets:

- `CLOUDFLARE_API_TOKEN` — Cloudflare token with **Pages: Edit**
- `CLOUDFLARE_ACCOUNT_ID` — run `npx wrangler@4 whoami` to read it

Alternative: Cloudflare dashboard → Workers & Pages → `orbit-music` → Settings →
Builds & deployments → Connect to Git (`jainnaitk/ORBIT`), Production branch `main`,
**Root directory `website`**, build command blank, output directory `website`.
Setting the root to `website` is what makes Cloudflare compile the Function.
Do not enable both options at once.

## Secrets / config

- This site has **no secrets and no env vars**; do not add any.
- Never put a Cloudflare API token, `.env`, or GitHub token in `website/`.
- `wrangler.toml` is not needed for Pages; project settings live in the dashboard/CLI args.

## Rollback

Dashboard → Workers & Pages → `orbit-music` → Deployments → ⋯ → Rollback.
Or redeploy the previous commit. Rollback does not touch the GitHub release the Function reads.

## Report back

Deployment URL, production URL, whether `functions/api/download.js` was included,
live status for `/` and `/api/download`, and the current latest release tag (or "none yet").