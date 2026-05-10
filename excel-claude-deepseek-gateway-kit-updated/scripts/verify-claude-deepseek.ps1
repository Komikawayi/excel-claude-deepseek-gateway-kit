$ErrorActionPreference = 'Continue'

Write-Host '== Claude Code -> DeepSeek 验证 ==' -ForegroundColor Cyan

function Show-Env($Name) {
  $v = [Environment]::GetEnvironmentVariable($Name, 'User')
  if ([string]::IsNullOrWhiteSpace($v)) {
    Write-Host "[MISS] $Name (User)"
    return $null
  } else {
    if ($Name -eq 'ANTHROPIC_AUTH_TOKEN') {
      $mask = if ($v.Length -gt 8) { $v.Substring(0,4) + '...' + $v.Substring($v.Length-4) } else { '***' }
      Write-Host "[OK] $Name=$mask"
    } else {
      Write-Host "[OK] $Name=$v"
    }
    return $v
  }
}

$vars = @(
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_AUTH_TOKEN',
  'ANTHROPIC_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'CLAUDE_CODE_SUBAGENT_MODEL'
)

Write-Host '[1/4] 检查用户级环境变量'
$envMap = @{}
$vars | ForEach-Object { $envMap[$_] = Show-Env $_ }

Write-Host '[2/4] 检查 claude 命令'
if (Get-Command claude -ErrorAction SilentlyContinue) {
  claude --version
} else {
  Write-Host '[FAIL] claude 命令不可用（可能 PATH 未生效）' -ForegroundColor Red
}

Write-Host '[3/4] 检查 DeepSeek 基础连通（带认证）'
$token = $envMap['ANTHROPIC_AUTH_TOKEN']
if ([string]::IsNullOrWhiteSpace($token)) {
  Write-Host '[WARN] 没有检测到 ANTHROPIC_AUTH_TOKEN，跳过认证连通测试'
} else {
  try {
    $resp = Invoke-WebRequest -Uri 'https://api.deepseek.com/anthropic/v1/messages' -Method Post -Headers @{
      'Content-Type'='application/json';
      'Authorization'="Bearer $token";
      'x-api-key'=$token
    } -Body '{"model":"deepseek-v4-pro","max_tokens":1,"messages":[{"role":"user","content":"x"}]}' -TimeoutSec 20
    Write-Host "[INFO] 直连返回: $($resp.StatusCode)"
  } catch {
    if ($_.Exception.Response) {
      $code = [int]$_.Exception.Response.StatusCode
      if ($code -eq 401) {
        Write-Host '[WARN] 返回 401：Key 可能失效/余额异常/权限异常，请在 DeepSeek 控制台核验。'
      } else {
        Write-Host "[INFO] 直连返回异常码: $code"
      }
    } else {
      Write-Host "[WARN] 无法直连 DeepSeek: $($_.Exception.Message)"
    }
  }
}

Write-Host '[4/4] Claude 最小调用测试（需要已正确配置）'
try {
  claude -p "Reply with exactly: OK"
} catch {
  Write-Host "[FAIL] Claude 调用失败: $($_.Exception.Message)" -ForegroundColor Red
}
