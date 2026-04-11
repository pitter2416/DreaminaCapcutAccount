# DreaminaCapcutAccount（自动化框架模板）

这是一个**通用的浏览器自动化框架**，用于你**自有/已授权**站点的注册流程回归测试。

特性：
- 读取 `accounts.txt`（每行 `email: password`）
- 可配置并发、步骤延迟、随机抖动、连续失败限流（暂停/降并发/恢复）
- 遇到 OTP/验证码步骤时支持两种模式：
  - **自动抓码**：对接你自有的验证码接口（邮件/短信均可），抓到 6 位混合码后自动填写并尝试提交
  - **人工输入**：检测到 OTP 页面后暂停，等待你在浏览器里手动输入并提交
- 输出 `Results/` 成功/失败记录，支持重复运行跳过已成功账号

## 安装

在本目录创建虚拟环境并安装依赖：

```bash
cd /Users/tuanhui/developer/workspace/DreaminaCapcutAccount
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

安装浏览器（缓存固定到项目内 `.playwright-browsers`）：

```bash
python install_browsers.py
```

## 配置

- 编辑 `config.json` 的 `target_url` 为你已授权站点的注册入口。
- 如需开启自动抓码，在 `config.json` 的 `sms` 段配置（字段见文件内默认值）：
  - `sms.enabled`: `true/false`
  - `sms.endpoint_url`: 例如 `https://2925.com/mailv2/maildata/MailList/mails`
  - `sms.token`: Bearer Token
  - `sms.timeout_seconds`: 等待验证码超时（秒）
  - `sms.trace_id_prefix`: 用于接口日志追踪（最终 trace_id 会拼接随机后缀）
  - `sms.tls_verify`: 自签名证书测试环境可设为 `false`
- 把账号写入 `accounts.txt`（不会被提交到 git）：  
  `someone@example.com: P@ssw0rd!`

## 运行

```bash
python main.py
```

按 `Ctrl+C` 会停止提交新任务，等待当前任务收尾后退出。

## 结果与排查

- **结果文件**：默认输出到 `Results/`
  - `Results/success.txt`：成功账号
  - `Results/fail.txt`：失败账号 + 原因
  - `Results/status.jsonl`：结构化状态记录
- **失败截图**：当流程超时/异常时，会自动截图到：
  - `Results/screenshots/<run_id>/...png`
  - 并在 `fail.txt` / `status.jsonl` 的 `reason` 字段中附带 `screenshot=...` 路径

常见问题：

- **接口 401/403**：检查 `config.json` 的 `sms.token` 是否正确/是否过期，确认请求头为 `authorization: Bearer <token>`。
- **自签名证书报错**：测试环境可将 `sms.tls_verify` 设为 `false`（不建议生产使用）。
- **验证码提取不到**：确认接口返回 `result.list[].subject` 中包含类似 `verification code is R9WB6X` 或 `验证码: R9WB6X` 文案；如不一致需要调整 `sms_helper.py` 的正则。
- **OTP 输入框形态不匹配**：当前默认覆盖“单输入框（get_by_label）”与“分格输入框（one-time-code / inputmode / tel）”；如你站点使用自定义组件/iframe，建议前端加 `data-testid` 并在 `flows/registration_flow.py` 中改为更精确的选择器。

# DreaminaCapcutAccount
