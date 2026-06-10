$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PidFile = Join-Path $ScriptDir ".gp_ai_windows.pid"

function Stop-ByPidFile {
    if (-not (Test-Path $PidFile)) {
        return $false
    }

    $pidLine = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $rawPid = if ($pidLine) { $pidLine.Trim() } else { "" }
    if (-not ($rawPid -match "^\d+$")) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $false
    }

    $process = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $false
    }

    Stop-Process -Id $process.Id -Force
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "GP_AI stopped. PID: $rawPid" -ForegroundColor Green
    return $true
}

if (Stop-ByPidFile) {
    exit 0
}

$matches = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "uvicorn" -and
        $_.CommandLine -match "app\.main:app"
    }

if (-not $matches) {
    Write-Host "GP_AI was not running." -ForegroundColor Yellow
    exit 0
}

foreach ($match in $matches) {
    Stop-Process -Id $match.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped GP_AI process. PID: $($match.ProcessId)" -ForegroundColor Green
}

Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
