# mac 适配版回合并原项目指南

更新时间：2026-05-09

## 1. 目标

把当前 `excel-claude-deepseek-gateway-kit-mac-20260509` 中的 mac 适配能力，整理成一个干净、可 review、可回滚的 Pull Request，合并回原始项目。

原始项目目录：

- `excel-claude-deepseek-gateway-kit-20260508/`

mac 适配目录：

- `excel-claude-deepseek-gateway-kit-mac-20260509/`

## 2. 先明确合并策略

建议不要把整个 `excel-claude-deepseek-gateway-kit-mac-20260509/` 目录原样提 PR。

更推荐的做法是：

1. 在原项目基础上新增 mac 支持，而不是再保留一个并行的完整副本目录。
2. 把“跨平台代码改动”和“mac 新增脚本/文档”拆成清晰的文件级变更。
3. 保留 Windows 启动方式，同时新增 mac 启动方式，形成双平台支持。

## 3. 本次建议纳入 PR 的变更

### 3.1 代码与配置

- `gateway/app/main.py`
  - 增加更灵活的 CORS 处理
  - 允许 `null` Origin
  - 支持 `ALLOWED_ORIGINS=*`
- `gateway/.env.example`
  - 增加 mac/HTTPS 相关配置项
  - 增加 `GATEWAY_HOST`
  - 增加 `ENABLE_HTTPS`
  - 增加 `SSL_CERT_FILE`
  - 增加 `SSL_KEY_FILE`
- `gateway/.gitignore`
  - 忽略 `.env`
  - 忽略 `.venv`
  - 忽略 `gateway.log`
  - 忽略 `.gateway.pid`
  - 忽略 `certs/`

### 3.2 新增 mac 脚本

- `gateway/run-gateway.sh`
- `gateway/start-gateway.sh`
- `gateway/stop-gateway.sh`
- `gateway/generate-dev-cert.sh`
- `gateway/trust-dev-ca.sh`

### 3.3 文档

- `README.md`
- `gateway/README.md`
- `docs/excel-claude-deepseek-gateway-sop.md`
- `docs/claude-deepseek-mac-experience-guide.md`

## 4. 不要纳入 PR 的内容

以下内容是本地运行产物或敏感信息，必须排除：

- `gateway/.env`
- `gateway/.venv/`
- `gateway/gateway.log`
- `gateway/.gateway.pid`
- `gateway/certs/`
- `__pycache__/`
- 任何真实 API Key

## 5. 推荐 PR 拆分方式

如果你希望 review 更顺畅，建议拆成 2 个提交：

### 提交 1：mac 基础支持

包含：

- `gateway/app/main.py`
- `gateway/.env.example`
- `gateway/.gitignore`
- `gateway/run-gateway.sh`
- `gateway/start-gateway.sh`
- `gateway/stop-gateway.sh`
- `gateway/README.md`
- `README.md`

目标：

- 让原项目具备 mac 启动、后台运行、CORS 兼容能力

### 提交 2：HTTPS 与文档补充

包含：

- `gateway/generate-dev-cert.sh`
- `gateway/trust-dev-ca.sh`
- `docs/excel-claude-deepseek-gateway-sop.md`
- `docs/claude-deepseek-mac-experience-guide.md`

目标：

- 解决 Office for Mac 要求 HTTPS 的使用场景
- 补齐文档和排障说明

## 6. 实际提 PR 的推荐流程

### 方案 A：你有原仓库写权限

1. 在真正的原仓库根目录执行：

```bash
git checkout main
git pull origin main
git checkout -b feat/macos-gateway-support
```

2. 把以下文件从 mac 目录复制到原项目对应位置：

```text
gateway/app/main.py
gateway/.env.example
gateway/.gitignore
gateway/run-gateway.sh
gateway/start-gateway.sh
gateway/stop-gateway.sh
gateway/generate-dev-cert.sh
gateway/trust-dev-ca.sh
gateway/README.md
docs/excel-claude-deepseek-gateway-sop.md
docs/claude-deepseek-mac-experience-guide.md
README.md
```

3. 保留原来的 Windows 文件，不要删：

```text
gateway/run-gateway.ps1
gateway/start-gateway.bat
gateway/stop-gateway.bat
docs/claude-deepseek-windows-experience-guide.md
```

4. 检查不要误提交本地文件：

```bash
git status
```

5. 提交：

```bash
git add .
git commit -m "feat: add macOS gateway scripts and HTTPS support"
git push origin feat/macos-gateway-support
```

6. 在 GitHub 发起 Pull Request。

### 方案 B：你没有原仓库写权限

1. Fork 原仓库
2. 克隆你自己的 fork
3. 新建分支
4. 按上面的文件清单复制变更
5. 推送到你的 fork
6. 从 fork 向上游仓库提 PR

## 7. 提 PR 前自检清单

- 原项目的 Windows 脚本仍然保留
- mac 脚本有执行权限
- `README` 同时说明 Windows 和 mac
- `.env` 没有提交
- `certs/` 没有提交
- `gateway.log` 没有提交
- `deepseek` 真实密钥没有出现在任何文档
- `http` 和 `https` 的使用说明一致

## 8. 建议的 Reviewer 关注点

- 现有 Windows 使用方式是否无回归
- mac 脚本是否最小侵入
- HTTPS 证书方案是否足够明确
- CORS 放宽策略是否合理
- 文档是否同时覆盖 Word/Excel for Mac 场景

## 9. 这次变更的核心价值

- 原项目从 Windows-only 变成 Windows + macOS
- 兼容 Office for Mac 对 HTTPS 的要求
- 兼容 Office WebView 的 `null` Origin 预检
- 降低 mac 用户接入 DeepSeek Gateway 的门槛
