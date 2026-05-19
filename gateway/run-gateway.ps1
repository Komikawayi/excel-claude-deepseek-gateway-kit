# Run from this folder:
#   powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1

$ErrorActionPreference = 'Stop'

if (!(Test-Path .\.env) -and (Test-Path .\.env.example)) {
  Copy-Item .\.env.example .\.env
  Write-Host 'Created .env from .env.example. Please fill DEEPSEEK_API_KEY first.' -ForegroundColor Yellow
}

if (!(Test-Path .\.venv)) {
  python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

$envFile = Join-Path (Get-Location) '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^[A-Za-z_][A-Za-z0-9_]*=') {
      $parts = $_.Split('=',2)
      if ($parts.Count -eq 2) {
        [Environment]::SetEnvironmentVariable($parts[0], $parts[1])
      }
    }
  }
}

$port = if ($env:GATEWAY_PORT) { $env:GATEWAY_PORT } else { '8787' }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $port --no-use-colors
