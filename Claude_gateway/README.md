# Claude Gateway

Claude 兼容网关，将 Anthropic Messages API 请求转发到 DeepSeek / Kimi / MiMo 等上游。

Excel 插件发送 `sonnet` / `opus`，网关自动映射到对应上游模型（历史 `haiku` 请求会兼容映射到 `sonnet` 档）。

## 安装

```bash
# 方式一：pip 安装（推荐）
pip install -e .

# 方式二：仅装依赖
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env`，填入 API key：

```bash
cp .env.example .env
```

关键配置项：

```env
# 选择 provider：deepseek / kimi / mimo / auto
ACTIVE_PROVIDER=deepseek

# 填入对应的 API key
DEEPSEEK_API_KEY=sk-你的密钥
```

## 启动

```bash
# 方式一：CLI 命令（pip install 后可用）
claude-gateway --provider deepseek --port 8790

# 方式二：uvicorn
uvicorn --app-dir src claude_gateway.main:app --host 127.0.0.1 --port 8790

# 方式三：PowerShell 脚本
.\run-gateway.ps1
```

启动后将 Excel 插件的 API endpoint 改为 `http://127.0.0.1:8790`。

## 模型映射

| Excel 发送 | DeepSeek | Kimi | MiMo |
|------------|----------|------|------|
| `opus` / `claude-opus-4-5` | deepseek-v4-pro | kimi-k2.6 | mimo-v2.5-pro |
| `sonnet` / `claude-sonnet-4-5` | deepseek-v4-flash | kimi-k2.5 | mimo-v2.5 |

未识别的 `claude-*` 模型名会按前缀匹配（`claude-sonnet*` → mid 档，`claude-haiku*` 兼容映射到 mid 档）。

## Auto 模式

`ACTIVE_PROVIDER=auto` 时，网关根据请求中的 API key 前缀自动选择 provider：

| Key 前缀 | 路由目标 |
|----------|---------|
| `dk-*` | DeepSeek |
| `sk-kimi-*` | Kimi codingplan |
| `sk-mimo-*` | MiMo PAYG |
| `tp-*` | MiMo Token Plan |
| `sk-*` | MiMo PAYG（默认） |

## Web Search（实验开关）

默认关闭：`ENABLE_WEB_SEARCH_TOOL=false`。  
开启后：网关会放通并透传 Anthropic `web_search_*` server tool 相关结构（如 `web_search_20250305`、`server_tool_use`、`web_search_tool_result`）。

可选增强（默认开启）：

```env
# 非流式模式下，若上游先返回 tool_use(web_search)，网关会自动执行搜索并发起第二轮请求
ENABLE_AUTO_WEB_SEARCH_EXECUTION=true
AUTO_WEB_SEARCH_MAX_RESULTS=5
AUTO_WEB_SEARCH_TIMEOUT_SECONDS=20
AUTO_WEB_SEARCH_MAX_ROUNDS=2
```

注意：
1. `ENABLE_AUTO_WEB_SEARCH_EXECUTION=false` 时，这是“透传兼容”能力；是否真正联网由上游 provider 决定。  
2. `ENABLE_AUTO_WEB_SEARCH_EXECUTION=true` 时，网关会在非流式场景自动执行本地搜索回路（DuckDuckGo HTML）并补 `tool_result`，可通过 `AUTO_WEB_SEARCH_MAX_ROUNDS` 控制最多补几轮。  
3. 开关关闭时，相关块会被 sanitize 过滤，行为与历史版本一致。

协议说明（重要）：
1. 自动执行开启时，网关会将 `web_search_*` 强制规范成 client tool（`name/input_schema`），避免 server tool 与 client tool 混用。
2. 网关补完首轮 `tool_result` 后，会在后续轮次强制 `tool_choice: none` 且移除 `tools`，减少反复 `tool_use` 不收敛的问题。

## 新增 Provider

1. 在 `src/claude_gateway/providers/` 下创建新类，继承 `ProviderConfig`
2. 实现 `resolve_upstream_url()` 和 `route_model()`
3. 在 `PROVIDER_REGISTRY` 中注册

## 测试

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
