Set-Location E:\mystockagent
$deadline = (Get-Date).AddHours(2.5)
while ((Get-Date) -lt $deadline) {
    $out = python -X utf8 _netprobe.py 2>$null
    if ($out -match 'PROBE_OK') {
        "NETWORK_OK at $(Get-Date -Format HH:mm:ss)"
        python -X utf8 cycle_system\ashare_v2\tools\forward_shadow.py fetch 2026-10-19 2>$null
        if (Test-Path output\ashare_v2\forward\incremental\2026-10-19.meta.json) {
            "FETCH_OK at $(Get-Date -Format HH:mm:ss)"
            python -X utf8 cycle_system\ashare_v2\tools\forward_shadow.py plan 2026-10-19 2>$null
            exit 0
        }
        "fetch failed despite probe ok at $(Get-Date -Format HH:mm:ss)"
    }
    "no-network at $(Get-Date -Format HH:mm:ss), sleep 300"
    Start-Sleep -Seconds 300
}
"NETWORK_NOT_RECOVERED by $(Get-Date -Format HH:mm:ss)"
exit 2
