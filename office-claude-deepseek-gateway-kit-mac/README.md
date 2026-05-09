# Excel Claude + DeepSeek Gateway Kit for macOS

更新时间：2026-05-09

这个目录是面向 macOS 的最小完整包，包含：

- 网关源码（FastAPI）
- mac 启停脚本（shell）
- 使用文档（mac 实战总结 + SOP）
- 安全扫描结果（Bandit + pip-audit）

## 目录结构

- `docs/`
  - `claude-deepseek-mac-experience-guide.md`
  - `excel-claude-deepseek-gateway-sop.md`
- `gateway/`
  - `app/main.py`
  - `app/log_mw.py`
  - `requirements.txt`
  - `generate-dev-cert.sh`
  - `trust-dev-ca.sh`
  - `run-gateway.sh`
  - `start-gateway.sh`
  - `stop-gateway.sh`
  - `.env.example`
  - `.gitignore`
  - `README.md`
- `reports/`
  - `bandit-report-final.json`
  - `pip-audit-report-final.json`

## 快速开始

1. 进入 `gateway/`
2. 复制 `.env.example` 为 `.env`
3. 按需填入 `DEEPSEEK_API_KEY`
4. 如果 Office 要求 HTTPS，先生成并信任本地开发证书：

```bash
./generate-dev-cert.sh
./trust-dev-ca.sh
```

5. 首次执行：

```bash
chmod +x ./*.sh
./run-gateway.sh
```

6. 在 Excel Claude 里配置 Gateway：

- URL: 如果启用了 HTTPS，就填 `https://<你的地址>:8787`
- Token: 如果 `.env` 已填 `DEEPSEEK_API_KEY`，这里可填任意非空；如果 `.env` 未填，则这里填真实 DeepSeek Key

## 后台启动

```bash
./start-gateway.sh
./stop-gateway.sh
```

## 安全说明

本包默认建议排除以下本地文件：

- `.env`
- `.venv`
- `gateway.log`
- `.gateway.pid`
- Python 缓存目录

上传前请再次确认仓库中没有任何真实 API Key。
