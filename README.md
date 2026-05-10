# Office Claude + DeepSeek Gateway Kit

更新时间：2026-05-10
目前已经支持win和mac两个版本，按需下载即可

添加对macos的支持

## 目录结构

- `docs/`
  - `claude-deepseek-windows-experience-guide.md`
  - `excel-claude-deepseek-gateway-sop.md`
- `gateway/`
  - `app/main.py`
  - `app/log_mw.py`
  - `requirements.txt`
  - `run-gateway.ps1`
  - `start-gateway.bat`
  - `stop-gateway.bat`
  - `.env.example`
  - `README.md`
- `reports/`
  - `bandit-report-final.json`
  - `pip-audit-report-final.json`

## 快速开始

1. 进入 `gateway/`
2. 复制 `.env.example` 为 `.env` 并填入 `DEEPSEEK_API_KEY`
3. 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1
```

4. 在 Excel Claude 里配置 Gateway：

- URL: `http://127.0.0.1:8787`
- Token: 任意非空（或填 DeepSeek Key）

## 安全说明

本包已经排除：

- `.env`（真实密钥）
- `.venv`（本地虚拟环境）
- 运行日志临时文件

上传前请再次确认仓库中没有任何真实 API Key。
