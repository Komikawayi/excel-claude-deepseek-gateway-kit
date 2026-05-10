# 新电脑一键切换 Claude Code -> DeepSeek

## 1. 前置要求

- 已安装 Node.js（含 npm）
- 可访问 npm 源
- PowerShell 可用

## 2. 一键配置（只填 API Key）

在本仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-claude-deepseek.ps1 -DeepSeekApiKey "sk-你的key"
```

可选参数：

- `-ModelPrimary` 默认 `deepseek-v4-pro`
- `-ModelFast` 默认 `deepseek-v4-flash`
- `-DeepSeekBaseUrl` 默认 `https://api.deepseek.com/anthropic`
- `-SkipInstall` 跳过 `npm install -g @anthropic-ai/claude-code@latest`

示例：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-claude-deepseek.ps1 -DeepSeekApiKey "sk-xxx" -SkipInstall
```

## 3. 验证

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-claude-deepseek.ps1
```

## 4. 常见问题

1. `claude` 命令找不到：关闭并重开终端。
2. `Unable to connect to Anthropic services`：通常是没切到 DeepSeek 或环境变量未生效，先跑验证脚本。
3. 像素宠物乱码：终端渲染问题，不影响模型连通。

## 5. 安全提示

- 不要把 API Key 提交到 GitHub。
- 泄露后请立刻在 DeepSeek 后台轮换。
