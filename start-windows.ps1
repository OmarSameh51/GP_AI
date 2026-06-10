param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PidFile = Join-Path $ScriptDir ".gp_ai_windows.pid"
$LogFile = Join-Path $ScriptDir "gp_ai_windows.log"
$ErrLogFile = Join-Path $ScriptDir "gp_ai_windows.err.log"

function Fail($Message) {
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    if (Test-Path $ErrLogFile) {
        Write-Host ""
        Write-Host "Last FastAPI error log lines:" -ForegroundColor Yellow
        Get-Content $ErrLogFile -Tail 30 -ErrorAction SilentlyContinue
    }
    exit 1
}

function Info($Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Get-DotEnvValue($Name, $DefaultValue) {
    $envFile = Join-Path $ScriptDir ".env"
    if (-not (Test-Path $envFile)) {
        return $DefaultValue
    }

    $line = Get-Content $envFile |
        Where-Object { $_ -match "^\s*$Name\s*=" } |
        Select-Object -First 1

    if (-not $line) {
        return $DefaultValue
    }

    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Assert-DotEnvValue($Name) {
    $value = Get-DotEnvValue $Name ""
    if ([string]::IsNullOrWhiteSpace($value)) {
        Fail "GP_AI\.env is missing required value '$Name'."
    }
    return $value
}

function Test-Http($Url) {
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Http($Url, $Name, $Seconds) {
    for ($i = 1; $i -le $Seconds; $i++) {
        if (Test-Http $Url) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @($py.Source, "-3")
    }

    return $null
}

function Get-OllamaPath {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollama) {
        return $ollama.Source
    }

    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
    }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

function Invoke-Checked($Command, $Arguments, $ErrorMessage) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail $ErrorMessage
    }
}

if (-not (Test-Path (Join-Path $ScriptDir ".env"))) {
    Fail "GP_AI\.env is missing. Copy .env.example to .env and fill MONGO_URI, NEO4J_*, and INTERNAL_KEY."
}

if (-not (Test-Path (Join-Path $ScriptDir "requirements.txt"))) {
    Fail "requirements.txt was not found in GP_AI."
}

if (-not (Test-Path (Join-Path $ScriptDir "app\main.py"))) {
    Fail "app\main.py was not found. Run this script from the GP_AI folder or keep it beside the app folder."
}

Assert-DotEnvValue "MONGO_URI" | Out-Null
Assert-DotEnvValue "NEO4J_URI" | Out-Null
Assert-DotEnvValue "NEO4J_USERNAME" | Out-Null
Assert-DotEnvValue "NEO4J_PASSWORD" | Out-Null
Assert-DotEnvValue "INTERNAL_KEY" | Out-Null

$Port = Get-DotEnvValue "PORT" "9100"
$OllamaUrl = Get-DotEnvValue "OLLAMA_URL" "http://localhost:11434"
$OllamaModel = Get-DotEnvValue "OLLAMA_MODEL" "phi"

Info "Checking Python..."
$pythonParts = @(Get-PythonCommand)
if (-not $pythonParts) {
    Fail "Python was not found. Install Python 3.11+ from python.org, then reopen PowerShell."
}

$pythonCommand = $pythonParts[0]
$pythonBaseArgs = @()
if ($pythonParts.Count -gt 1) {
    $pythonBaseArgs = $pythonParts[1..($pythonParts.Count - 1)]
}

$versionArgs = $pythonBaseArgs + @("--version")
Invoke-Checked $pythonCommand $versionArgs "Python exists, but it could not run."

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Info "Creating virtual environment..."
    $venvArgs = $pythonBaseArgs + @("-m", "venv", ".venv")
    Invoke-Checked $pythonCommand $venvArgs "Failed to create .venv. Check your Python installation."
}

Info "Checking Python dependencies..."
if (-not $SkipDependencyInstall) {
    Invoke-Checked $VenvPython @("-m", "pip", "install", "-r", "requirements.txt") "Failed to install Python dependencies from requirements.txt. Check your internet connection and pip output."
}

Invoke-Checked $VenvPython @("-c", "import fastapi, uvicorn, httpx, neo4j, motor, pydantic, yaml, dotenv") "Some Python dependencies are still missing. Run: .\.venv\Scripts\python.exe -m pip install -r requirements.txt"

if ($OllamaUrl -match "^http://(localhost|127\.0\.0\.1):11434") {
    Info "Checking Ollama..."
    $ollamaPath = Get-OllamaPath
    if (-not $ollamaPath) {
        Fail "Ollama was not found. Install Ollama for Windows from https://ollama.com/download, then reopen PowerShell."
    }

    if (-not (Test-Http "$OllamaUrl/api/tags")) {
        Info "Starting Ollama..."
        $ollamaOut = Join-Path $env:TEMP "gp_ai_ollama.out.log"
        $ollamaErr = Join-Path $env:TEMP "gp_ai_ollama.err.log"
        Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Minimized -RedirectStandardOutput $ollamaOut -RedirectStandardError $ollamaErr | Out-Null

        if (-not (Wait-Http "$OllamaUrl/api/tags" "Ollama" 30)) {
            Fail "Ollama did not start on $OllamaUrl. Try opening the Ollama app manually or run: ollama serve"
        }
    }

    Info "Checking Ollama model '$OllamaModel'..."
    $models = & $ollamaPath list
    if ($LASTEXITCODE -ne 0) {
        Fail "Ollama is running, but 'ollama list' failed."
    }

    $escapedModel = [regex]::Escape($OllamaModel)
    $modelPattern = if ($OllamaModel.Contains(":")) { "(?m)^$escapedModel\s" } else { "(?m)^$escapedModel(:\S+)?\s" }
    if (-not ($models -match $modelPattern)) {
        Info "Model '$OllamaModel' is not installed. Pulling it now..."
        Invoke-Checked $ollamaPath @("pull", $OllamaModel) "Failed to pull Ollama model '$OllamaModel'. Check your internet connection and available disk space."
    }
} else {
    Info "OLLAMA_URL is not local ($OllamaUrl). Checking that it responds..."
    if (-not (Test-Http "$OllamaUrl/api/tags")) {
        Fail "OLLAMA_URL '$OllamaUrl' is not reachable. Fix GP_AI\.env or start that Ollama server."
    }
}

$HealthUrl = "http://localhost:$Port/healthz"
if (Test-Http $HealthUrl) {
    Info "GP_AI is already running on port $Port."
    Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing | Select-Object -ExpandProperty Content
    exit 0
}

Info "Starting GP_AI FastAPI on port $Port..."
Remove-Item $LogFile, $ErrLogFile -Force -ErrorAction SilentlyContinue

$process = Start-Process `
    -FilePath $VenvPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", $Port) `
    -WorkingDirectory $ScriptDir `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLogFile `
    -PassThru

Set-Content -Path $PidFile -Value $process.Id

if (-not (Wait-Http $HealthUrl "GP_AI" 30)) {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Fail "GP_AI did not start on $HealthUrl. Check .env values for MongoDB, Neo4j, INTERNAL_KEY, and Ollama."
}

Info "GP_AI is running."
Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing | Select-Object -ExpandProperty Content
Write-Host "PID: $($process.Id)"
Write-Host "Logs: $LogFile"
Write-Host "Errors: $ErrLogFile"
