<#
.SYNOPSIS
  Inkstone one-click launcher (PowerShell / Windows).

.DESCRIPTION
  Uses uv to install dependencies, loads AGNES_API_KEY from .env, and runs the
  comic generator.

.PARAMETER Args
  Forwarded to examples/generate_comic.py (e.g. my_novel.txt --out out --format webtoon).

.EXAMPLE
  .\scripts\start.ps1
  .\scripts\start.ps1 examples/scene1.txt --out comic_out --format webtoon
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required. Install from https://docs.astral.sh/uv/"
    exit 1
}

uv sync --extra dev --quiet

# ------------------------------------------------------------------ #
# Load AGNES_API_KEY from .env
# ------------------------------------------------------------------ #
if (-not $env:AGNES_API_KEY -and (Test-Path .env)) {
    Get-Content .env | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $key, $value = $line.Split("=", 2)
            $key = $key.Trim()
            $value = $value.Trim().Trim('"').Trim("'")
            if ($key -and -not (Get-Item "env:$key" -ErrorAction SilentlyContinue)) {
                Set-Item "env:$key" $value
            }
        }
    }
}

if (-not $env:AGNES_API_KEY) {
    Write-Error "AGNES_API_KEY is not set. Put it in .env (AGNES_API_KEY=sk-xxx) or set it via `$env:AGNES_API_KEY."
    exit 1
}

uv run python examples/generate_comic.py @args
