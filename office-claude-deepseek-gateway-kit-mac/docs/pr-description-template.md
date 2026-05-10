# Pull Request 文案模板

下面内容可以直接复制到 GitHub Pull Request 页面，再按需要微调。

## PR 标题

```text
feat: add macOS gateway scripts and HTTPS support
```

## PR 正文

```markdown
## 变更摘要

这个 PR 为现有 Excel Claude + DeepSeek Gateway 项目补充 macOS 支持，并保持 Windows 用法不变。

本次改动主要包含：

- 新增 macOS 启动、后台启动、停止脚本
- 新增本地 HTTPS 证书生成与信任脚本
- 放宽 Office for Mac WebView 所需的 CORS 兼容逻辑
- 更新 README、SOP 和 mac 实战文档

## 主要改动

### 代码

- 更新 `gateway/app/main.py`
  - 支持更灵活的 `ALLOWED_ORIGINS`
  - 默认兼容 `null` Origin
  - 提升 Office for Mac 预检请求兼容性

### 配置

- 更新 `gateway/.env.example`
  - 增加 `GATEWAY_HOST`
  - 增加 `ENABLE_HTTPS`
  - 增加 `SSL_CERT_FILE`
  - 增加 `SSL_KEY_FILE`

- 更新 `gateway/.gitignore`
  - 忽略 `.env`
  - 忽略 `.venv`
  - 忽略 `gateway.log`
  - 忽略 `.gateway.pid`
  - 忽略 `certs/`

### macOS 新增脚本

- `gateway/run-gateway.sh`
- `gateway/start-gateway.sh`
- `gateway/stop-gateway.sh`
- `gateway/generate-dev-cert.sh`
- `gateway/trust-dev-ca.sh`

### 文档

- 更新 `README.md`
- 更新 `gateway/README.md`
- 更新 `docs/excel-claude-deepseek-gateway-sop.md`
- 新增 `docs/claude-deepseek-mac-experience-guide.md`

## 兼容性说明

- 保留原有 Windows 脚本：
  - `gateway/run-gateway.ps1`
  - `gateway/start-gateway.bat`
  - `gateway/stop-gateway.bat`
- 本 PR 目标是新增 macOS 支持，不移除现有 Windows 使用方式

## 为什么需要这次改动

当前项目文档和脚本主要面向 Windows。
在 Office for Mac 场景中，存在以下实际问题：

- 只能接受 HTTPS Gateway URL
- WebView 预检可能带 `Origin: null`
- `127.0.0.1` 在部分环境中不可直接访问

本 PR 解决了以上问题，使项目可以在 macOS 上落地。

## 验证

已完成以下验证：

- macOS 下脚本语法检查通过
- 网关可在 macOS 下正常启动/停止
- HTTPS 本地证书生成成功
- HTTPS `GET /healthz` 正常
- HTTPS `OPTIONS /v1/models` 正常
- HTTPS `GET /v1/models` 正常

## 风险与注意事项

- `ALLOWED_ORIGINS=*` 仅建议作为排障或本地开发配置
- 本地开发证书依赖 macOS 钥匙串信任
- 提交时不应包含 `.env`、`.venv`、`certs/`、`gateway.log`

## 后续建议

- 后续可继续补充统一的跨平台文档结构
- 可考虑把 Windows/macOS 启动逻辑进一步收敛成更统一的入口
```

## 建议附加评论

如果 reviewer 比较关注范围控制，可以额外补一条评论：

```markdown
这次 PR 尽量保持最小侵入：

- 不删除现有 Windows 脚本
- 不改变上游代理核心逻辑
- 主要新增 macOS 启动能力、HTTPS 能力和文档补充
```
