# Excel Claude + 本地 Gateway + DeepSeek（个人版）SOP

更新时间：2026-05-08  
适用人群：个人本机试用（非组织级统一部署）

## 1. 目标

在 Excel 的 Claude 面板中，使用 `Gateway` 模式通过本地兼容层接入 DeepSeek，跑通最小可用闭环并提升稳定性。

## 2. 固定配置

- 连接方式：`Gateway`
- Gateway URL：`http://127.0.0.1:8787`
- Token：DeepSeek API Key（建议新建 Office 专用 Key）

## 3. Excel 内操作步骤

1. 打开 Excel，进入 Claude 面板。
2. 选择 `Connect another way`。
3. 选择标签 `Gateway`（不要选 Vertex/Bedrock/Azure）。
4. 在 `Gateway URL` 输入：

```text
http://127.0.0.1:8787
```

5. 在 `Token` 输入你的 DeepSeek API Key。
6. 点击 `Continue`。

## 4. 首次验收（最小闭环）

连接后至少做 3 个请求：

1. 短问答：让 Claude 用一句话解释当前选中单元格内容。
2. 长文本总结：让 Claude 总结一段较长文本（> 150 字）。
3. 表格解释：让 Claude 对一个小表（至少 3 列）给出趋势描述。

通过标准：

- 三个请求都返回正常内容；
- 无认证错误弹窗；
- 连续使用 10~15 分钟无明显掉线。

## 5. 故障排查顺序（按顺序执行）

1. **检查 Key**：是否有效、是否有余额。
2. **检查 URL**：必须是 `http://127.0.0.1:8787`（当前本机网关方案）。
3. **检查网络**：公司代理/防火墙是否拦截 `api.deepseek.com`。
4. **检查组织策略**：是否禁用了第三方 Gateway（需 IT 放行）。
5. **检查本地网关是否存活**：`GET /healthz` 应返回 `{"status":"ok"}`。
6. **检查网关日志**：关注 `POST /v1/messages` 是否出现 `502`、`gateway stream error`、`gateway malformed sse data`。

## 6. 常见错误码对照

- `401/403`：Key 错误、权限不足或被禁用。
- `402`：余额不足。
- `429`：触发限流（频率过高）。
- `timeout / network error`：网络、DNS、代理或防火墙问题。
- `502`：本地网关到上游 DeepSeek 链路异常（超时、连接中断、上游不可用）。

## 7. 安全建议（必须）

1. 不要复用已泄露过的 Key。
2. 为 Office 场景单独创建一个专用 Key。
3. 定期轮换 Key（建议 30~90 天）。
4. 不在截图、聊天记录、文档中明文粘贴 Key。
5. 网关默认只监听 `127.0.0.1`，避免直接暴露在局域网。

## 8. 回退方案

如果 DeepSeek 连接不稳定：

1. 返回 Claude 面板连接页。
2. 切回官方默认登录路径（Anthropic 官方连接）。
3. 保留本 SOP 与测试结果，后续再评估是否改为自建网关。

## 9. 本机预检（可选但推荐）

先运行网关：

```powershell
cd D:\scrapling_study\gateway
powershell -ExecutionPolicy Bypass -File .\run-gateway.ps1
```
#这里需要切换自己电脑的路径

另开一个终端执行：

```powershell
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/v1/models
```

通过标准：

- `/healthz` 返回 `{"status":"ok"}`；
- `GET /v1/models` 返回 200；
- `POST /v1/messages` 最小探测请求返回 200；
- 响应头允许 `Origin: https://pivot.claude.ai`。

## 10. 本机验证记录（2026-05-08）

执行命令（本地网关方案）：

```powershell
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/v1/models
```

结果：

- `/healthz`：`200`
- `/v1/models`：`200`
- `POST /v1/messages`（最小请求）：`200`
- 结论：在本机网络环境下，本地网关方案可稳定提供 Excel Claude 所需兼容接口。
