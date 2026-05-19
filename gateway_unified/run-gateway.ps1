# 统一网关启动脚本
# 用法:
#   powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1          # 日常启动
#   powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1 -Install # 首次安装依赖

param(
    [switch]$Install
)

$ErrorActionPreference = 'Stop'

if (!(Test-Path .\.env) -and (Test-Path .\.env.example)) {
  Copy-Item .\.env.example .\.env
  Write-Host 'Created .env from .env.example. Please fill API keys first.' -ForegroundColor Yellow
}

if (!(Test-Path .\.venv)) {
  Write-Host 'Creating virtual environment...' -ForegroundColor Cyan
  python -m venv .venv
  $Install = $true
}

if ($Install) {
  Write-Host 'Installing/updating dependencies...' -ForegroundColor Cyan
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -e .
}

$envFile = Join-Path (Get-Location) '.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^[A-Za-z_][A-Za-z0-9_]*=') {
      $parts = $_.Split('=',2)
      if ($parts.Count -eq 2) {
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
          if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
          }
        }
        [Environment]::SetEnvironmentVariable($name, $value)
      }
    }
  }
}

$port = if ($env:GATEWAY_PORT) { $env:GATEWAY_PORT } else { '8790' }
Write-Host "Starting gateway on port $port ..." -ForegroundColor Green
.\.venv\Scripts\python.exe -m uvicorn claude_gateway.main:app --host 127.0.0.1 --port $port --no-use-colors
