Set-Location E:\mystockagent
$deadline = (Get-Date).AddHours(4.5)
while ((Get-Date) -lt $deadline) {
    python -X utf8 cycle_system\ashare_v2\tools\forward_shadow.py fetch 2026-10-19 2>$null | Out-Null
    if (Test-Path output\ashare_v2\forward\incremental\2026-10-19.meta.json) {
        "FETCH_OK at $(Get-Date -Format HH:mm:ss)"
        python -X utf8 cycle_system\ashare_v2\tools\forward_shadow.py plan 2026-10-19 2>$null
        exit 0
    }
    "no-bar yet at $(Get-Date -Format HH:mm:ss), sleep 600"
    Start-Sleep -Seconds 600
}
"BARS_NOT_LANDED by $(Get-Date -Format HH:mm:ss)"
exit 2
