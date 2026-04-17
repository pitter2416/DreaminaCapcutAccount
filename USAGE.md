# Dreamina CapCut 账号自动注册工具 - 使用指南

## 🚀 快速开始

### 基本用法

```bash
# 使用默认配置运行
python3 main.py

# 生成10个成功账号后停止
python3 main.py -t 10

# 生成100个成功账号，5个并发
python3 main.py -t 100 -c 5

# 无头模式运行（不显示浏览器窗口）
python3 main.py -t 50 --headless

# 有头模式运行（显示浏览器窗口，便于调试）
python3 main.py -t 5 --no-headless
```

## 📋 命令行参数

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--target-success` | `-t` | 目标成功账号数量 | config.json中的值 |
| `--concurrent` | `-c` | 并发数量 | config.json中的值 |
| `--headless` | - | 无头模式运行 | config.json中的值 |
| `--no-headless` | - | 有头模式运行 | - |
| `--max-tasks` | `-m` | 最大任务数（0=无限） | config.json中的值 |

## 💡 使用场景

### 场景1：批量生产账号

```bash
# 生产100个成功账号，3个并发，无头模式
python3 main.py -t 100 -c 3 --headless
```

**输出示例：**
```
============================================================
[Result] 运行结果统计
============================================================
  运行模式: 持续模式
  目标成功数: 100
  实际成功数: 100
  失败数: 12
  已提交任务: 112

✅ 成功！已达到目标成功账号数量: 100/100
📦 成功账号已保存到: Results/success_accounts.txt

============================================================
📋 成功账号列表（共 100 个）
============================================================
  1. abcdefgh@ai-job.online: Abc1234567
  2. xyz12345@ai-job.online: Xyz9876543
  ...
  100. test9999@ai-job.online: Test123456
============================================================

📄 纯文本格式（方便复制）:
------------------------------------------------------------
abcdefgh@ai-job.online: Abc1234567
xyz12345@ai-job.online: Xyz9876543
...
test9999@ai-job.online: Test123456
------------------------------------------------------------
```

### 场景2：快速测试验证

```bash
# 只注册1个账号，验证流程
python3 main.py -t 1 --no-headless
```

### 场景3：无人值守运行

```bash
# 后台运行，生成1000个账号
nohup python3 main.py -t 1000 -c 5 --headless > output.log 2>&1 &

# 查看进度
tail -f output.log
```

## ⚙️ 配置文件

编辑 `config.json` 设置默认参数：

```json
{
  "target_success_count": 1,      // 默认目标成功数
  "max_tasks": 0,                  // 0=无限运行
  "concurrent_flows": 1,           // 默认并发数
  "headless": true,                // 默认无头模式
  
  // ... 其他配置
}
```

## 📊 程序特性

### 1. 智能账号管理
- ✅ 首次启动自动检测账号文件
- ✅ 账号用尽时自动生成10000个新账号
- ✅ 自动跳过已成功注册的账号
- ✅ 失败限流和自动恢复

### 2. 401自动恢复
- ✅ 检测到Token过期自动登录
- ✅ 支持API登录和浏览器登录
- ✅ 自动捕获新的Bearer Token
- ✅ 无缝继续运行

### 3. 详细的结果输出
- ✅ 实时显示注册进度
- ✅ 结束时输出所有成功账号
- ✅ 提供编号列表和纯文本两种格式
- ✅ 成功账号保存到 `Results/success_accounts.txt`

## 🎯 工作流程

```
启动程序
  ↓
检查 accounts.txt
  ↓ (如果为空)
自动生成 10000 个账号
  ↓
开始注册流程（并发执行）
  ↓
每完成一个任务 → 更新统计
  ↓
成功数量 >= target_success_count？
  ↓ 是
显示详细统计
  ↓
输出所有成功账号
  ↓
保存到 success_accounts.txt
  ↓
程序退出 ✅
```

## 📁 输出文件

### Results/success_accounts.txt
成功账号列表，格式：`email:password`

```
abcdefgh@ai-job.online: Abc1234567
xyz12345@ai-job.online: Xyz9876543
```

### Results/status.jsonl
所有任务的详细状态记录（JSON Lines格式）

### Results/success.txt
仅包含成功账号的邮箱地址

### Results/fail.txt
失败账号及原因

## 🔧 高级用法

### 组合使用参数

```bash
# 大规模生产：500个账号，10个并发，无头模式
python3 main.py -t 500 -c 10 --headless

# 小规模测试：5个账号，1个并发，显示浏览器
python3 main.py -t 5 -c 1 --no-headless

# 中等规模：50个账号，3个并发
python3 main.py -t 50 -c 3
```

### 监控运行状态

```bash
# 实时查看日志
tail -f output.log

# 查看成功账号数量
wc -l Results/success_accounts.txt

# 查看最新成功的账号
tail -5 Results/success_accounts.txt
```

## ⚠️ 注意事项

1. **首次运行**：如果 `accounts.txt` 为空，程序会自动生成10000个账号
2. **并发控制**：建议根据网络情况调整并发数（1-10之间）
3. **无头模式**：生产环境建议使用 `--headless` 提高性能
4. **调试模式**：遇到问题时使用 `--no-headless` 观察浏览器操作
5. **中断运行**：按 `Ctrl+C` 安全停止，会等待当前任务完成

## 🐛 故障排查

### 问题1：程序卡在某个步骤
```bash
# 使用有头模式观察
python3 main.py -t 1 --no-headless
```

### 问题2：401错误频繁出现
- 程序会自动处理，无需手动干预
- 检查网络连接是否正常

### 问题3：成功率低
- 降低并发数：`-c 1`
- 增加延迟：修改 `config.json` 中的 `step_delay_ms`

## 📞 技术支持

如有问题，请查看：
- 日志输出
- `Results/` 目录下的截图和日志文件
- `Results/status.jsonl` 中的详细记录

---

**祝使用愉快！** 🎉
