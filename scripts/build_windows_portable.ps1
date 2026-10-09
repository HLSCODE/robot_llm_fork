param([string]$OutputDirectory = "dist")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    # Build-only tools are isolated by uv; project dependencies remain locked.
    & uv run --frozen --link-mode=copy --extra full --with-requirements packaging/windows/requirements.txt python scripts/build_windows_portable.py --output $OutputDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Windows portable build failed (exit $LASTEXITCODE)"
    }
} finally {
    Pop-Location
}
