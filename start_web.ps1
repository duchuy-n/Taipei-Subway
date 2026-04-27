param(
    [int]$Port = 8010
)

function Test-PortInUse {
    param(
        [int]$CandidatePort
    )

    $listener = Get-NetTCPConnection -LocalPort $CandidatePort -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $listener)
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$SelectedPort = $Port
while ((Test-PortInUse $SelectedPort)) {
    Write-Output "[WARN] Port $SelectedPort is already in use. Trying $($SelectedPort + 1)..."
    $SelectedPort += 1
}

Write-Output "[INFO] Starting app on http://127.0.0.1:$SelectedPort"
python scripts\build_gis_runtime_cache.py --check-only *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Output "[INFO] GIS runtime artifact is missing or stale. Prebuilding cache..."
    python scripts\build_gis_runtime_cache.py
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to prebuild GIS runtime artifact."
        exit $LASTEXITCODE
    }
}
python -m uvicorn app.main:app --host 127.0.0.1 --port $SelectedPort --reload
