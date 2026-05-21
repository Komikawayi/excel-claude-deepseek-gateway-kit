# Claude Gateway macOS

一个面向 Office for Mac 与 Claude 兼容客户端的本地 Anthropic Messages API 网关。

这个版本是从 `gateway_unified` 复制出的独立 macOS 变体，目标是解决 macOS 下最常见的三类问题：

1. 启动链路要原生支持 macOS。
2. Office for Mac 的 CORS / `Origin: null` 预检不能再被拦截。
3. 即使没有终端窗口，也要能通过日志文件和 `/v1/info` 诊断当前网关状态。

---

## 1. 与统一版的区别

| 项目 | `gateway_unified` | `gateway_macos` |
|---|---|---|
| 目标场景 | Windows / 统一版 | macOS / Office for Mac |
| 默认端口 | `8790` | `8890` |
| CLI | `claude-gateway` | `claude-gateway-macos` |
| Python 包 | `claude_gateway` | `claude_gateway_macos` |
| 启动脚本 | `run-gateway.ps1` | `run-gateway.sh` / `run-gateway.command` |
| 诊断接口 | 无 | `GET /v1/info` |
| 默认 CORS | 单 origin 思路 | 多 origin，含 `null` |

这两个版本可以在同一台机器上并存运行。

---

## 2. 快速开始

### 2.1 安装

```bash
cd gateway_macos
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

### 2.2 配置

```bash
cp .env.example .env
```

至少填写：

```env
ACTIVE_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
GATEWAY_PORT=8890
GATEWAY_LOG_FILE=./gateway.log
```

### 2.3 启动

方式 1：CLI

```bash
claude-gateway-macos --provider deepseek --port 8890
```

方式 2：直接 uvicorn

```bash
uvicorn --app-dir src claude_gateway_macos.main:app --host 127.0.0.1 --port 8890
```

方式 3：macOS 脚本

```bash
./run-gateway.sh
```

方式 4：Finder 双击

```text
run-gateway.command
```

方式 5：根仓库 npm 包装入口

```bash
cd ..
npm run gateway:macos:install
npm run gateway:macos:start
```

---

## 3. 对外接口

固定接口：

1. `GET /healthz`
2. `GET /v1/models`
3. `POST /v1/messages`
4. `GET /v1/info`

健康检查：

```bash
curl http://127.0.0.1:8890/healthz
```

诊断信息：

```bash
curl http://127.0.0.1:8890/v1/info
```

`/v1/info` 会返回：

1. 当前 provider
2. 脱敏后的 API key
3. 当前 base URL / 区域基址
4. 当前模型映射
5. 当前 CORS origin 列表
6. 当前日志文件路径
7. 当前端口

---

## 4. macOS 兼容策略

### 4.1 CORS

`ALLOWED_ORIGIN` 现在支持三种模式：

1. 留空：使用内置默认值
2. 逗号分隔：例如 `https://pivot.claude.ai,null`
3. `*`：完全放开

默认值覆盖 Office for Mac 常见场景：

1. `https://pivot.claude.ai`
2. `null`
3. `https://localhost`
4. `https://localhost:3000`
5. `https://localhost:5173`
6. `https://appsource.microsoft.com`
7. `https://store.office.com`

这里最重要的是 `null`，因为 Office for Mac 的 WebView 在某些场景下会以 `Origin: null` 发起预检。

### 4.2 文件日志

如果配置了：

```env
GATEWAY_LOG_FILE=./gateway.log
```

网关会把 `stdout` / `stderr` 同时写入日志文件。即使你通过 Finder 或桌面入口启动，看不到终端窗口，也可以直接检查：

```bash
tail -f gateway.log
```

### 4.3 启动诊断

启动时会输出：

1. provider
2. 端口
3. 日志文件路径
4. CORS origins
5. Web Search 开关
6. 请求体大小限制

---

## 5. Provider 与模型

支持：

1. `deepseek`
2. `kimi`
3. `mimo`
4. `minimax`
5. `auto`

默认模型映射保持和统一版一致：

| Claude 别名 | DeepSeek | Kimi | MiMo | MiniMax |
|---|---|---|---|---|
| `opus` | `deepseek-v4-pro` | `kimi-k2.6` | `mimo-v2.5-pro` | `MiniMax-M2.7` |
| `sonnet` | `deepseek-v4-flash` | `kimi-k2.5` | `mimo-v2.5` | `MiniMax-M2.5` |
| `haiku`（兼容） | sonnet 档 | sonnet 档 | sonnet 档 | `MiniMax-M2.5-highspeed` |

`/v1/models` 当前仍只对外暴露 `opus` 和 `sonnet` 两档。

---

## 6. Web Search 现状

macOS 版保留统一版的 Web Search 能力：

1. 可透传 `web_search_*`
2. 可在非流式模式下执行本地自动工具回路
3. 支持 XML `<tool_call>` 兼容
4. DuckDuckGo HTML 搜索层有 1 次快速重试

已知限制：

1. 自动工具回路只在非流式模式启用
2. 搜索质量仍依赖外网可达性
3. `ConnectTimeout` 仍可能出现，但会先快速重试一次

---

## 7. 推荐配置

### 7.1 DeepSeek

```env
ACTIVE_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
GATEWAY_PORT=8890
GATEWAY_LOG_FILE=./gateway.log
```

### 7.2 Kimi

```env
ACTIVE_PROVIDER=kimi
KIMI_API_KEY=sk-kimi-xxx
KIMI_CODING_BASE_URL=https://api.kimi.com/coding/
KIMI_PAYG_BASE_URL=https://api.moonshot.cn/anthropic
CODINGPLAN_MODEL=kimi-for-coding
GATEWAY_PORT=8890
```

### 7.3 MiMo

```env
ACTIVE_PROVIDER=mimo
MIMO_API_KEY=tp-xxx
MIMO_TP_REGION=cn
GATEWAY_PORT=8890
```

### 7.4 MiniMax

```env
ACTIVE_PROVIDER=minimax
MINIMAX_API_KEY=sk-cp-xxx
MINIMAX_REGION=cn
GATEWAY_PORT=8890
```

---

## 8. 常见排障

### 8.1 Excel / Word for Mac 提示无法连接

先看：

1. `curl http://127.0.0.1:8890/healthz`
2. `curl http://127.0.0.1:8890/v1/info`
3. `tail -f gateway.log`

如果日志里完全没有请求：

1. 优先怀疑 CORS / 预检未通过
2. 检查 Office 端是否以 `Origin: null` 发起请求
3. 确认 `.env` 没有把 `ALLOWED_ORIGIN` 锁死成单个旧值

### 8.2 启动后窗口闪退或无输出

1. 优先用 `./run-gateway.sh` 从终端启动
2. 检查 `gateway.log`
3. 检查 Python 3.11+ 是否可用

### 8.3 想确认现在到底连的是谁

直接访问：

```bash
curl http://127.0.0.1:8890/v1/info
```

这个接口就是给现场排障准备的。

---

## 9. 测试

运行：

```bash
.venv/bin/python -m pytest tests -v
```

当前回归覆盖包括：

1. Provider 路由与 key 前缀
2. 模型映射
3. 输入清洗
4. Web Search 回路
5. `Origin: null` 的 CORS 预检
6. `/v1/info` 的结构与 key 脱敏
7. macOS 独立 CLI 路径

---

## 10. 安全建议

1. 不要提交 `.env`
2. 不要提交 `gateway.log`
3. 不要把真实 `sk-` / `tp-` / `Bearer` 写进 README 或脚本
4. 生产环境若要收窄 CORS，请显式设置 `ALLOWED_ORIGIN`
