# Stop all dubbing services (WLK, IndexTTS2, glue).
# Run:  powershell -ExecutionPolicy Bypass -File stop_services.ps1
$ports = @(8000, 50001, 5100)
foreach ($p in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        foreach ($pid_ in $conn.OwningProcess) {
            Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped process $pid_ (port $p)"
        }
    }
}
Get-CimInstance Win32_Process -Filter "Name='wlk.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "All services stopped."
