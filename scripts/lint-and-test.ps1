param(
    [switch]$FixFormatting
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'vidbrain' }
if ($running) {
    Write-Host ""
    Write-Host "VidBrain daemon is running and will interfere with this script (DB locks, CPU contention)." -ForegroundColor Yellow
    Write-Host "Stop the daemon with Ctrl+C first, then re-run lint-and-test." -ForegroundColor Yellow
    Write-Host ""
    foreach ($proc in $running) {
        Write-Host "  PID $($proc.ProcessId): $($proc.CommandLine)" -ForegroundColor DarkGray
    }
    Write-Host ""
    exit 1
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
}

function Invoke-PythonModule {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        $commandText = "python " + ($Arguments -join " ")
        throw "$commandText failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -Path "pyproject.toml")) {
    throw "Please run this script from the repository root."
}

if ($FixFormatting) {
    Invoke-Step -Name "Auto-format Python files with black" -Action {
        Invoke-PythonModule -Arguments @("-m", "black", "--fast", "src", "tests")
    }
} else {
    Invoke-Step -Name "Check code formatting with black" -Action {
        Invoke-PythonModule -Arguments @("-m", "black", "--fast", "--check", "src", "tests")
    }
}

Invoke-Step -Name "Lint with ruff" -Action {
    Invoke-PythonModule -Arguments @("-m", "ruff", "check", "src", "tests")
}

Invoke-Step -Name "Run tests with coverage" -Action {
    Invoke-PythonModule -Arguments @(
        "-m", "pytest",
        "tests",
        "-v",
        "--cov=src",
        "--cov-report=term-missing",
        "--cov-report=xml:coverage.xml"
    )
}

Write-Host ""
Write-Host "Local lint-and-test passed." -ForegroundColor Green
