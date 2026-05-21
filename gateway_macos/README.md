# Claude Gateway Unified

一个面向 Office 插件与 Claude 兼容客户端的 Anthropic Messages API 网关。  
网关对外保持 Anthropic 接口形态（`/v1/models`、`/v1/messages`），对内可路由到 DeepSeek / Kimi / MiMo / MiniMax。

当前版本重点是：
1. 单一入口统一接入多个上游。
2. 模型别名稳定映射（Opus/Sonnet 双档对外发现）。
3. Web Search 在“自动执行模式”下的协议收敛（避免 server/client tool 混用）。
4. 输入清洗、日志脱敏、大小限制与错误泛化。

---

## 1. 快速开始

### 1.1 安装

```bash
# 推荐：可编辑安装（包含 CLI 命令）
pip install -e .

# 或仅安装运行依赖
pip install -r requirements.txt
```

### 1.2 配置

复制模板并填写：

```bash
cp .env.example .env
```

至少需要设置：

```env
ACTIVE_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
```

### 1.3 启动

```bash
# 方式 1：CLI
claude-gateway --provider deepseek --port 8790

# 方式 2：uvicorn
uvicorn --app-dir src claude_gateway.main:app --host 127.0.0.1 --port 8790

# 方式 3：Windows 脚本
.\run-gateway.ps1
```

默认健康检查：

```bash
curl http://127.0.0.1:8790/healthz
```

### 1.4 NPM 包装入口（可选）

说明：网关本体仍是 Python；`npm` 仅作为命令包装层，方便 Node 用户统一操作。

```bash
cd ..
npm run gateway:install
npm run gateway:start
```

---

## 2. 对外接口（固定）

网关不扩展私有路径，对外只保证以下接口：

1. `GET /healthz`
2. `GET /v1/models`（兼容 `GET /models`）
3. `POST /v1/messages`

这让 Office、Claude 兼容 SDK、自研客户端都能共用同一入口。

---

## 3. Provider 与路由规则

支持四种运行模式：

1. `ACTIVE_PROVIDER=deepseek`
2. `ACTIVE_PROVIDER=kimi`
3. `ACTIVE_PROVIDER=mimo`
4. `ACTIVE_PROVIDER=minimax`
5. `ACTIVE_PROVIDER=auto`

### 3.1 DeepSeek 模式

1. 只接受 `sk-*` incoming key（若没配置 env key）。
2. 上游地址默认：`https://api.deepseek.com/anthropic`
3. 路由 kind：`deepseek`

### 3.2 Kimi 模式

1. `sk-kimi-*` -> Coding Plan（`KIMI_CODING_BASE_URL`）
2. 其他 `sk-*` -> PAYG（`KIMI_PAYG_BASE_URL`）
3. Coding Plan 强制模型：`kimi-for-coding`
4. Kimi 支持图片输入（`image` / `image_url`）

### 3.3 MiMo 模式

1. `sk-*` -> PAYG（`MIMO_PAYG_BASE_URL`）
2. `tp-*` -> Token Plan（按区域 `cn/sgp/ams`）
3. 区域可用头覆盖：`x-mimo-tp-region`

### 3.4 Auto 模式

`ACTIVE_PROVIDER=auto` 时按 key 前缀分流：

1. `dk-*` -> DeepSeek
2. `sk-kimi-*` -> Kimi codingplan
3. `sk-mimo-*` -> MiMo PAYG
4. `tp-*` -> MiMo Token Plan
5. `sk-*` -> MiMo PAYG（默认）

重要限制：

1. Auto 模式下如果配置了某个 provider 的 env key，会“锁定流量到该 provider”。
2. Auto 模式不能仅凭通用 `sk-*` 无歧义地区分 Kimi PAYG 与 MiMo PAYG（设计上默认给 MiMo）。

### 3.5 MiniMax 模式

1. `sk-api-*` -> PAYG（按量计费）
2. `sk-cp-*` -> Coding Plan / Token Plan
3. 区域基址默认由 `MINIMAX_REGION=cn|global` 决定
4. 可用请求头覆盖区域：`x-minimax-region=cn|global`
5. 当前保持 `auto` 现状，不自动识别 MiniMax 前缀，避免影响 8790 现网路由

---

## 4. 模型发现与映射策略

### 4.1 `/v1/models` 对外暴露

当前只对外暴露双档：

1. `claude-opus-4-5` / `opus`
2. `claude-sonnet-4-5` / `sonnet`

说明：

1. `haiku` 不再在模型列表中展示。
2. 旧客户端若仍发送 `haiku` 或 `claude-haiku-*`，网关会兼容映射到 Sonnet 档。

### 4.2 Provider 默认映射

| Claude 别名 | DeepSeek | Kimi | MiMo | MiniMax |
|---|---|---|---|---|
| `opus` | `deepseek-v4-pro` | `kimi-k2.6` | `mimo-v2.5-pro` | `MiniMax-M2.7` |
| `sonnet` | `deepseek-v4-flash` | `kimi-k2.5` | `mimo-v2.5` | `MiniMax-M2.5` |
| `haiku`（兼容） | 映射到 sonnet 档 | 映射到 sonnet 档 | 映射到 sonnet 档 | `MiniMax-M2.5-highspeed` |

### 4.3 任务推荐模型

#### 高复杂推理 / 代码审查 / 长文本结构化产出

1. DeepSeek：`opus -> deepseek-v4-pro`
2. Kimi：`opus -> kimi-k2.6`
3. MiMo：`opus -> mimo-v2.5-pro`

#### 日常问答 / 中等复杂内容生成 / 普通自动化

1. DeepSeek：`sonnet -> deepseek-v4-flash`
2. Kimi：`sonnet -> kimi-k2.5`
3. MiMo：`sonnet -> mimo-v2.5`

#### 编码主用链路（Kimi）

1. 使用 `sk-kimi-*` 让路由进入 Coding Plan。
2. 模型固定为 `kimi-for-coding`，不依赖客户端显式传 model。

---

## 5. Web Search 能力说明（重点）

Web Search 相关开关：

```env
ENABLE_WEB_SEARCH_TOOL=false
ENABLE_AUTO_WEB_SEARCH_EXECUTION=true
AUTO_WEB_SEARCH_MAX_RESULTS=5
AUTO_WEB_SEARCH_TIMEOUT_SECONDS=20
AUTO_WEB_SEARCH_MAX_ROUNDS=2
```

### 5.1 两种模式

#### 模式 A：仅透传（上游自行执行）

1. `ENABLE_WEB_SEARCH_TOOL=true`
2. `ENABLE_AUTO_WEB_SEARCH_EXECUTION=false`

行为：

1. 网关透传 `web_search_*`、`server_tool_use`、`web_search_tool_result` 结构。
2. 是否真正联网完全取决于上游 provider 的兼容实现。

#### 模式 B：网关自动执行（推荐当前默认）

1. `ENABLE_WEB_SEARCH_TOOL=true`
2. `ENABLE_AUTO_WEB_SEARCH_EXECUTION=true`

行为：

1. 网关把 `web_search_*` 强制规范成 client tool（`name/input_schema`）。
2. 只要能从上游响应提取出 web_search 调用（包括 XML `<tool_call>` 伪格式），就进入自动回路。
3. 回路中网关本地执行 DuckDuckGo HTML 搜索并回填 `tool_result`。
4. 首轮回填后强制 `tool_choice: none` 且移除 `tools`，避免重复 tool 调用不收敛。
5. 如果到上限仍出现 XML tool_call，网关用本地 `tool_result` 聚合文本兜底，避免 `<tool_call>` 直接漏给前端。

### 5.2 稳定性现状

已稳定：

1. 协议层不再混用 server tool/client tool。
2. `end_turn + <tool_call>` 这类非标准上游返回可被吞入回路处理。
3. 空 query 有 fallback（回退用户最后问题）。

仍不稳定（外部网络层）：

1. Web 搜索依赖外网可达性，偶发 `ConnectTimeout` 仍可能出现。
2. 已加入“1次快速重试（0.35s）”，可降低抖动但不能保证 100% 成功。
3. 超时场景下会返回失败摘要文本（例如 `web search failed: ConnectTimeout`）。

---

## 6. 输入清洗与安全机制

### 6.1 请求体限制

1. `MAX_REQUEST_BODY_BYTES` 默认 4MB（含 header 快速拒绝 + 流式读取兜底）。
2. 文本块、图片 base64、tool 描述、tool 输入深度、消息总条数均有限制。

### 6.2 内容清洗

1. 过滤不支持 content block（可按开关放通部分 web_search 结构）。
2. 修复/降级非法 `thinking` block。
3. 标准化 `tools`、`messages`、`system`、`max_tokens`。

### 6.3 日志与隐私

1. 默认日志脱敏（`LOG_CONTENT_REDACT=true`）。
2. 日志长度截断与 body 预览上限控制。
3. 上游错误对客户端做泛化（避免泄露上游细节），服务端日志保留排障信息。

---

## 7. 已知限制与不稳定项（建议公开）

1. Auto 模式下 `sk-*` 默认走 MiMo PAYG，无法自动精确区分 Kimi PAYG。
2. MiMo/Kimi/DeepSeek 上游对某些非标准 tool 输出的行为不完全一致。
3. Web Search 本地执行依赖 DuckDuckGo HTML 页面结构；若页面结构变化，需要调整解析器。
4. 本地搜索结果质量依赖公开搜索引擎可达性，受网络和地区策略影响明显。
5. 流式场景下 Web Search 自动回路不启用（当前自动回路仅用于非流式）。

---

## 8. 配置参考（推荐起步）

### 8.1 MiMo 主用（Office 常见）

```env
ACTIVE_PROVIDER=mimo
GATEWAY_PORT=8790
MIMO_API_KEY=tp-xxx
MIMO_TP_REGION=cn

ENABLE_WEB_SEARCH_TOOL=true
ENABLE_AUTO_WEB_SEARCH_EXECUTION=true
AUTO_WEB_SEARCH_MAX_RESULTS=5
AUTO_WEB_SEARCH_TIMEOUT_SECONDS=20
AUTO_WEB_SEARCH_MAX_ROUNDS=3
```

### 8.2 Kimi 主用（编码优先）

```env
ACTIVE_PROVIDER=kimi
KIMI_API_KEY=sk-kimi-xxx
KIMI_CODING_BASE_URL=https://api.kimi.com/coding/
KIMI_PAYG_BASE_URL=https://api.moonshot.cn/anthropic
CODINGPLAN_MODEL=kimi-for-coding
```

### 8.3 Auto 统一入口（多团队）

```env
ACTIVE_PROVIDER=auto
GATEWAY_PORT=8790
# 不建议同时填多个 provider env key，避免“流量锁定”
```

---

## 9. 常见排障

### 9.1 `/v1/messages` 返回 401

检查：

1. 是否配置了对应 provider 的 API key。
2. incoming key 前缀是否符合当前 provider 规则（如 DeepSeek 要 `sk-*`）。
3. Auto 模式是否被 env key 锁定到错误 provider。

### 9.2 返回 400 且 message 泛化

检查：

1. 请求体结构是否合法（messages/tools/thinking）。
2. 是否命中上游 thinking 连续对话约束（网关已尽量保留 reasoning block）。
3. 查看服务端日志中的 `[gateway upstream error]` 片段。

### 9.3 Web Search 结果偶发失败

检查：

1. 网络连通性与 DNS。
2. 是否频繁出现 `ConnectTimeout`（这属于外网层，不是协议层）。
3. 适度调大 `AUTO_WEB_SEARCH_TIMEOUT_SECONDS`，但会增加总延迟。

### 9.4 看到 `<tool_call>` 文本

当前版本理论上应被自动回路吞掉；若仍出现，收集：

1. 完整请求 body（去敏）。
2. 网关日志中 `auto_web_search` 相关行。
3. 对应响应 `stop_reason` 与 `content`。

---

## 10. 测试与质量门槛

运行测试：

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

当前测试重点覆盖：

1. Provider 路由与 key 前缀分类。
2. 模型别名映射与前缀兼容。
3. 输入清洗、大小限制、异常处理。
4. SSE 工具分片合并。
5. Web Search 自动回路的关键异常形态：
   - `tool_use` 正常回路
   - `end_turn + XML tool_call`
   - 连续 XML 多轮
   - 回路上限兜底不泄漏 XML
   - 搜索超时重试与重试耗尽

---

## 11. 目录结构

```text
gateway_unified/
├── src/claude_gateway/
│   ├── main.py        # FastAPI 入口、路由、自动工具回路
│   ├── providers.py   # DeepSeek/Kimi/MiMo/Auto 路由与模型映射
│   ├── sanitize.py    # 请求清洗与兼容转换
│   ├── stream.py      # SSE 修正与工具分片聚合
│   ├── log_mw.py      # 请求日志与脱敏
│   ├── models.py      # /v1/models 构建
│   └── web_search.py  # 本地 DuckDuckGo HTML 搜索 + 快速重试
├── tests/
├── .env.example
├── pyproject.toml
└── run-gateway.ps1
```

---

## 12. 安全发布建议

1. 永远不要上传 `.env`、真实密钥、运行日志。
2. 发布前执行一次敏感信息扫描（`sk-` / `tp-` / `Bearer`）。
3. 建议仅提交 `.env.example`，并保持注释与默认值同步。
4. 生产环境建议显式设置 `ALLOWED_ORIGIN`，并开启日志脱敏。

---

## 13. 版本说明（当前状态）

本 README 对应当前代码状态（含 Web Search 协议收敛与搜索重试增强）：

1. 自动执行开启时统一 client tool 协议。
2. 自动回路支持 XML tool_call 兼容。
3. 搜索层增加 1 次快速重试，降低偶发网络抖动失败率。
