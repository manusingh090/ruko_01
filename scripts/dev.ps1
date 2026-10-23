# Start the whole RUKO demo stack on Windows and connect the phone to it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1            # start everything
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Regenerate # rebuild data first
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Stop       # stop the services
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -BaitPump   # also pump the task-scam bait account
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -BuildConsole # rebuild the analyst console
#
# Steps: venv + requirements, synthetic data (if missing), mock intel on 8001,
# risk API with --demo-clock, mule pump, adb reverse so the phone's
# http://127.0.0.1:8000/ reaches the API. If port 8000 is taken on the laptop,
# the API moves to the next free port and adb reverse maps phone 8000 to it.

param(
    [int]$ApiPort = 8000,
    [int]$IntelPort = 8001,
    [int]$Payers = 40,
    [switch]$Regenerate,
    [switch]$NoPump,
    [switch]$BaitPump,
    [switch]$BuildConsole,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Logs = Join-Path $Root "logs"
$PidFile = Join-Path $Logs "dev.pids"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Stop-Stack {
    if (Test-Path $PidFile) {
        foreach ($id in Get-Content $PidFile) {
            try { Stop-Process -Id ([int]$id) -Force -ErrorAction Stop; Write-Host "stopped $id" } catch {}
        }
        Remove-Item $PidFile
    }
}

if ($Stop) { Stop-Stack; exit 0 }
Stop-Stack

function Test-PortFree([int]$port) {
    $l = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return -not $l
}

function Wait-Health([string]$url, [int]$seconds = 30) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { return Invoke-RestMethod -Uri $url -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500 }
    }
    throw "no answer from $url after $seconds s (see logs\*.log)"
}

# 1. Python venv
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "creating .venv"
    python -m venv .venv
    & $Py -m pip install -q -r backend\requirements.txt
}
# The API scores with the trained model (ml/model/predict.py), which needs LightGBM; without it the
# API falls back to the rules. ml\requirements.txt includes backend\requirements.txt.
& $Py -c "import lightgbm" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "installing ml/requirements.txt (LightGBM for the trained scorer)"
    & $Py -m pip install -q -r ml\requirements.txt
}
$env:PYTHONPATH = "backend;ml;data"
$env:PYTHONIOENCODING = "utf-8"

# 2. Synthetic world (data\*.parquet is git ignored)
if ($Regenerate -or -not (Test-Path "data\ledger.parquet")) {
    Write-Host "generating data"
    & $Py -m sim.generate --users 2000 --days 30 --seed 7 --out data/
    if ($LASTEXITCODE -ne 0) { throw "sim.generate failed" }
}

# 2b. Analyst console (console\dist is git ignored; the API serves it at /console/)
if ($BuildConsole -or -not (Test-Path "console\dist\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "building the analyst console"
        Push-Location console
        try {
            if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund | Out-Null }
            npm run build | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Host "console build failed; the API still runs without it" }
        } finally { Pop-Location }
    } else {
        Write-Host "npm not found; skipping the analyst console (install Node.js to get it)"
    }
}

# 3. Ports
if (-not (Test-PortFree $IntelPort)) { throw "port $IntelPort is taken; stop that process or pass -IntelPort" }
$phonePort = 8000
$wanted = $ApiPort
while ($ApiPort -eq $IntelPort -or -not (Test-PortFree $ApiPort)) { $ApiPort++ }
if ($ApiPort -ne $wanted) { Write-Host "port $wanted is taken, the API uses $ApiPort" }

# 4. Services
$intel = Start-Process -FilePath $Py -PassThru -WindowStyle Hidden `
    -ArgumentList "-m", "intel_mock.app", "--data", "data", "--port", "$IntelPort" `
    -RedirectStandardOutput "$Logs\intel.log" -RedirectStandardError "$Logs\intel.err.log"
$intel.Id | Out-File $PidFile -Encoding ascii
Wait-Health "http://127.0.0.1:$IntelPort/health" | Out-Null
Write-Host "mock intel up on $IntelPort"

$api = Start-Process -FilePath $Py -PassThru -WindowStyle Hidden `
    -ArgumentList "-m", "api.main", "--data", "data", "--port", "$ApiPort", `
                  "--intel", "http://127.0.0.1:$IntelPort", "--demo-clock" `
    -RedirectStandardOutput "$Logs\api.log" -RedirectStandardError "$Logs\api.err.log"
$api.Id | Out-File $PidFile -Append -Encoding ascii
$health = Wait-Health "http://127.0.0.1:$ApiPort/health"
Write-Host "risk API up on $ApiPort ($($health | ConvertTo-Json -Compress))"

# 5. Mule pump, so the demo mule looks live to the bank features
if (-not $NoPump) {
    & $Py -m sim.mule_pump --url "http://127.0.0.1:$IntelPort" --payers $Payers
    if ($LASTEXITCODE -ne 0) { throw "sim.mule_pump failed" }
}

# 5b. Task-scam bait account fanning out to 40 users, for the payment graph demo (/console/graph)
if ($BaitPump) {
    & $Py -m sim.bait_pump --api "http://127.0.0.1:$ApiPort" --intel "http://127.0.0.1:$IntelPort"
    if ($LASTEXITCODE -ne 0) { throw "sim.bait_pump failed" }
}

# 6. Phone
$Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path $Adb)) { $Adb = (Get-Command adb -ErrorAction SilentlyContinue).Source }
if ($Adb) {
    $devices = & $Adb devices | Select-String "\tdevice$"
    if ($devices) {
        & $Adb reverse "tcp:$phonePort" "tcp:$ApiPort" | Out-Null
        Write-Host "phone: adb reverse tcp:$phonePort -> laptop tcp:$ApiPort"
        # The mock NPCI's UPI number mapper (mobile -> UPI ID), as a real PSP app would call it.
        & $Adb reverse "tcp:8001" "tcp:$IntelPort" | Out-Null
        Write-Host "phone: adb reverse tcp:8001 -> laptop tcp:$IntelPort (UPI number mapper)"
        # The demo user's address book (people really paid in the world, plus a few never paid).
        & $Py -m sim.contacts --data data --adb | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host "phone: Meena's contacts are in the phone's Contacts" }
    } else {
        Write-Host "no phone or emulator connected; run later: adb reverse tcp:$phonePort tcp:$ApiPort"
    }
} else {
    Write-Host "adb not found; run later: adb reverse tcp:$phonePort tcp:$ApiPort"
}

Write-Host ""
Write-Host "Ready. In Cache Pay tap 'Check Scam Shield' or long press the title for the debug panel."
if (Test-Path "console\dist\index.html") { Write-Host "Analyst console: http://127.0.0.1:$ApiPort/console/" }
Write-Host "Logs: logs\api.log, logs\intel.log. Stop: scripts\dev.ps1 -Stop"
