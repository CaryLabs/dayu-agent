# Stop the dayu-agent Web UI (Streamlit) by matching its process command line.
$ErrorActionPreference = "SilentlyContinue"

$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match 'streamlit.*run.*streamlit_app' }

if (-not $procs) {
    Write-Host "No dayu-agent web process found. Nothing to stop."
    exit 0
}

$ids = $procs | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique
foreach ($id in $ids) {
    Write-Host "Stopping PID $id ..."
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}
Write-Host "Done."
