# Excel Claude + 本地 Gateway + DeepSeek（macOS）SOP

更新时间：2026-05-09  
适用人群：macOS 个人本机试用（非组织级统一部署）

## 1. 目标

在 Excel 的 Claude 面板中，使用 `Gateway` 模式通过本地兼容层接入 DeepSeek，跑通最小可用闭环并保持 macOS 下的日常可维护性。

## 2. 固定配置

- 连接方式：`Gateway`
- Gateway URL：`https://<本机地址>:8787`
- Token：如果本地 `.env` 已配置 `DEEPSEEK_API_KEY`，这里填任意非空；否则填真实 DeepSeek API Key

## 3. 首次启动

在 `gateway/` 目录执行：

```bash
cp .env.example .env
chmod +x ./*.sh
./generate-dev-cert.sh
./trust-dev-ca.sh
./run-gateway.sh
```

另开一个终端做预检：

```bash
curl https://<本机地址>:8787/healthz
curl https://<本机地址>:8787/v1/models
```

通过标准：

- `/healthz` 返回 `{"status":"ok"}`
- `/v1/models` 返回 200
- 网关启动日志未出现 `Upstream request failed`

## 4. Excel 内操作步骤

1. 打开 Excel，进入 Claude 面板。
2. 选择 `Connect another way`。
3. 选择标签 `Gateway`。
4. 在 `Gateway URL` 输入 `https://<本机地址>:8787`。
5. 在 `Token` 填写任意非空字符串或你的真实 DeepSeek Key。
6. 点击 `Continue`。

## 5. 首次验收（最小闭环）

连接后至少做 3 个请求：

1. 短问答：让 Claude 用一句话解释当前选中单元格内容。
2. 长文本总结：让 Claude 总结一段较长文本。
3. 表格解释：让 Claude 对一个小表给出趋势描述。

通过标准：

- 三个请求都返回正常内容
- 无认证错误弹窗
- 连续使用 10 分钟以上无明显掉线

## 6. 故障排查顺序

1. 检查 `DEEPSEEK_API_KEY` 或 Excel 中填写的 Token 是否有效。
2. 检查本地服务是否仍监听 `:8787`，并确认 HTTPS 证书已经生成且已被系统信任。
3. 检查公司代理、防火墙、DNS 是否拦截 `api.deepseek.com`。
4. 检查 `gateway.log` 或当前终端输出，重点关注 `502`、`gateway stream error`、`gateway malformed sse data`。
5. 检查 `/healthz` 与 `/v1/models` 是否仍返回 200。
6. 如果 Office for Mac 始终无法访问 `127.0.0.1`，将 `.env` 里的 `GATEWAY_HOST` 改为 `0.0.0.0`，然后把插件里的 Gateway URL 改成 `https://<本机局域网IP>:8787`。

## 7. 后台运行

```bash
./start-gateway.sh
./stop-gateway.sh
```

后台模式会生成：

- `gateway.log`：运行日志
- `.gateway.pid`：进程 PID 文件

## 8. 安全建议

1. 不要把真实 Key 提交到 Git。
2. 为 Office 场景单独创建一个专用 Key。
3. 定期轮换 Key。
4. 网关默认只监听 `127.0.0.1`，不要随意改为公网可访问地址。
