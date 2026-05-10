param(
  [Parameter(Mandatory = $true)]
  [string]$DeepSeekApiKey,
  [string]$ModelPrimary = 'deepseek-v4-pro',
  [string]$ModelFast = 'deepseek-v4-flash',
  [string]$DeepSeekBaseUrl = 'https://api.deepseek.com/anthropic',
  [switch]$SkipInstall,
  [switch]$AllowPlaceholderKey
)

$ErrorActionPreference = 'Stop'

function Set-UserEnv([string]$Name, [string]$Value) {
  [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
  Write-Host "[OK] $Name=$Value"
}

if ([string]::IsNullOrWhiteSpace($DeepSeekApiKey)) {
  throw 'DeepSeekApiKey 不能为空。'
}

if ($DeepSeekApiKey -notmatch '^sk-') {
  Write-Warning 'API Key 看起来不是 sk- 开头，请确认是否正确。'
}

if (
  -not $AllowPlaceholderKey -and
  (
    $DeepSeekApiKey -match 'placeholder' -or
    $DeepSeekApiKey -match 'test' -or
    $DeepSeekApiKey -match 'your[_-]?key'
  )
) {
  throw '检测到占位/测试 Key。为避免误覆盖真实配置，脚本已停止。请传入真实 DeepSeek Key。'
}

Write-Host '== Claude Code -> DeepSeek 一键配置开始 ==' -ForegroundColor Cyan

if (-not $SkipInstall) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw '未检测到 npm，请先安装 Node.js（含 npm）。'
  }

  Write-Host '[1/4] 安装/升级 Claude Code CLI...'
  npm install -g @anthropic-ai/claude-code@latest | Out-Host
} else {
  Write-Host '[1/4] 跳过安装（你传了 -SkipInstall）'
}

Write-Host '[2/4] 写入用户级环境变量...'
Set-UserEnv 'ANTHROPIC_BASE_URL' $DeepSeekBaseUrl
Set-UserEnv 'ANTHROPIC_AUTH_TOKEN' $DeepSeekApiKey
Set-UserEnv 'ANTHROPIC_MODEL' $ModelPrimary
Set-UserEnv 'ANTHROPIC_DEFAULT_OPUS_MODEL' $ModelPrimary
Set-UserEnv 'ANTHROPIC_DEFAULT_SONNET_MODEL' $ModelPrimary
Set-UserEnv 'ANTHROPIC_DEFAULT_HAIKU_MODEL' $ModelFast
Set-UserEnv 'CLAUDE_CODE_SUBAGENT_MODEL' $ModelFast
Set-UserEnv 'CLAUDE_CODE_EFFORT_LEVEL' 'max'
Set-UserEnv 'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK' '1'
Set-UserEnv 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC' '1'
Set-UserEnv 'CLAUDE_CODE_NO_FLICKER' '1'
Set-UserEnv 'TERM' 'xterm-256color'

Write-Host '[3/4] 当前会话注入同样变量（免重开立即生效）...'
$env:ANTHROPIC_BASE_URL = $DeepSeekBaseUrl
$env:ANTHROPIC_AUTH_TOKEN = $DeepSeekApiKey
$env:ANTHROPIC_MODEL = $ModelPrimary
$env:ANTHROPIC_DEFAULT_OPUS_MODEL = $ModelPrimary
$env:ANTHROPIC_DEFAULT_SONNET_MODEL = $ModelPrimary
$env:ANTHROPIC_DEFAULT_HAIKU_MODEL = $ModelFast
$env:CLAUDE_CODE_SUBAGENT_MODEL = $ModelFast
$env:CLAUDE_CODE_EFFORT_LEVEL = 'max'
$env:CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK = '1'
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'
$env:CLAUDE_CODE_NO_FLICKER = '1'
$env:TERM = 'xterm-256color'

Write-Host '[4/4] 基础验证...'
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  throw 'claude 命令不可用。请关闭并重新打开终端后重试。'
}

$ver = claude --version 2>&1
Write-Host "claude --version => $ver"

Write-Host ''
Write-Host '配置完成。建议下一步：' -ForegroundColor Green
Write-Host '1) 关闭当前终端并重新打开（确保用户级环境变量完全生效）'
Write-Host '2) 运行: claude -p "Reply with exactly: OK"'
Write-Host '3) 若失败，运行: powershell -ExecutionPolicy Bypass -File .\scripts\verify-claude-deepseek.ps1'
