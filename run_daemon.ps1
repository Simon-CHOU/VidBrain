<#
VidBrain 7x24 无人值守启动脚本
直接调用 Python，崩溃自动重启，PID 文件防重复启动
#>

param(
    [string]$VaultDir = "$PSScriptRoot\vidbrain_vault",
    [string]$Interval = "30m",
    [int]$BatchSize = 5,
    [int]$Cooldown = 0,
    [string]$ModelSize = "tiny",
    [int]$MetricsInterval = 3600
)

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$PidFile = Join-Path $LogDir "daemon.pid"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Singleton check
if (Test-Path $PidFile) {
    $existingPid = (Get-Content $PidFile -Raw).Trim()
    $procAlive = $false
    $isOurDaemon = $false
    $cmdName = ""

    try {
        $existingProc = Get-Process -Id ([int]$existingPid) -ErrorAction Stop
        if ($existingProc -and -not $existingProc.HasExited) {
            $procAlive = $true
            $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$existingPid"
            if ($wmi -and $wmi.CommandLine -and ($wmi.CommandLine -match 'run_daemon\.ps1')) {
                $isOurDaemon = $true
            } elseif ($wmi -and $wmi.CommandLine) {
                $cmdName = $wmi.CommandLine
            } else {
                $cmdName = $existingProc.ProcessName
            }
        }
    } catch {
        # Process no longer exists — treat as dead
    }

    if ($procAlive -and $isOurDaemon) {
        $msg = "[{0:yyyy-MM-dd HH:mm:ss}] WARN  Daemon already running (PID: $existingPid), refusing to start." -f (Get-Date)
        Write-Host $msg
        $msg | Out-File -Append -FilePath "$LogDir\daemon.log"
        exit 1
    }

    if ($procAlive -and -not $isOurDaemon) {
        $msg = "[{0:yyyy-MM-dd HH:mm:ss}] WARN  PID file points to non-VidBrain process (PID: $existingPid, cmd: $cmdName), removing stale PID file." -f (Get-Date)
        Write-Host $msg
        $msg | Out-File -Append -FilePath "$LogDir\daemon.log"
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
    } else {
        Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
    }
}
$PID | Out-File -FilePath $PidFile -Encoding ascii

$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { $pythonExe = $venvPython }
}

$restarts = 0
$maxRestart = 100

"[{0:yyyy-MM-dd HH:mm:ss}] INFO  VidBrain daemon starting (Python: $pythonExe, PID: $PID)" -f (Get-Date) | Tee-Object -Append "$LogDir\daemon.log"
"[{0:yyyy-MM-dd HH:mm:ss}] INFO  VaultDir=$VaultDir, Interval=$Interval, BatchSize=$BatchSize, Cooldown=$Cooldown" -f (Get-Date) | Tee-Object -Append "$LogDir\daemon.log"

while ($restarts -lt $maxRestart) {
    $start = Get-Date
    "[{0:yyyy-MM-dd HH:mm:ss}] INFO  Starting VidBrain (run #$($restarts+1))" -f (Get-Date) | Tee-Object -Append "$LogDir\daemon.log"

    $proc = Start-Process -FilePath $pythonExe -ArgumentList @(
        "-m", "src.main",
        "--vault-dir", $VaultDir,
        "--interval", $Interval,
        "--batch-size", $BatchSize,
        "--video-cooldown", $Cooldown,
        "--model-size", $ModelSize,
        "--metrics-interval", $MetricsInterval,
        "--metrics-export-dir", "reports",
        "--audit-export"
    ) -NoNewWindow -Wait -PassThru

    $elapsed = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)
    $restarts++

    if ($proc.ExitCode -eq 0) {
        "[{0:yyyy-MM-dd HH:mm:ss}] INFO  VidBrain exited normally (exit=0, uptime={1}min)" -f (Get-Date), $elapsed | Tee-Object -Append "$LogDir\daemon.log"
    } else {
        "[{0:yyyy-MM-dd HH:mm:ss}] WARN  VidBrain crashed (exit={0}, uptime={1}min)" -f $proc.ExitCode, $elapsed | Tee-Object -Append "$LogDir\daemon.log"
        $delay = [Math]::Min(10 * [Math]::Pow(2, [Math]::Min($restarts, 5)), 300)
        "[{0:yyyy-MM-dd HH:mm:ss}] INFO  Waiting {1}s before restart..." -f (Get-Date), $delay | Tee-Object -Append "$LogDir\daemon.log"
        Start-Sleep -Seconds $delay
    }
}

Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
"[{0:yyyy-MM-dd HH:mm:ss}] WARN  Max restarts ({0}) reached, daemon exiting" -f (Get-Date), $maxRestart | Tee-Object -Append "$LogDir\daemon.log"
