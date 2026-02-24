# EvoMap 批量注册工具 使用说明

## 快速开始

### 1. 安装依赖

```bash
pip install playwright requests
playwright install chromium
```

### 2. 准备数据文件

```bash
# 复制模板并填入真实数据
cp ../../data-templates/outlook令牌号.example.csv ../../data/outlook令牌号.csv
# 编辑邮箱文件，格式：email----password----client_id----refresh_token
```

### 3. 配置代理（可选）

**方式一：Mihomo 代理池（推荐）**
```bash
# 复制模板并配置
cp ../../data-templates/mihomo.example.json ../../data/mihomo.json
# 编辑配置：API地址、密钥、代理组、切换策略
# 设置 "enabled": true
```

**方式二：环境变量**
```bash
export HTTPS_PROXY=http://127.0.0.1:7890
```

### 4. 运行注册

**推荐：使用启动脚本**
```bash
cd ../..
python start.py
# 选择 EvoMap → 选择预检模式 → 开始注册
```

**直接运行**
```bash
python register.py --auto  # 自动模式
python register.py         # 交互模式
```

## 预检功能

预检用于验证已注册账号的邀请码状态，避免重复登录。

### 四种预检模式

| 模式 | 命令 | 说明 | 适用场景 |
|------|------|------|---------|
| 智能模式 | `--smart` | 只检查邀请码不完整的账号 | 默认，推荐 |
| 跳过模式 | `--skip` | 完全信任 state.json | 快速测试 |
| 完整模式 | `--full` | 检查所有已注册账号 | 全面验证 |
| 强制模式 | `--force` | 忽略 state.json，检查所有邮箱 | 数据不一致时 |

**使用方法**：
```bash
python preflight.py          # 智能模式（默认）
python preflight.py --skip   # 跳过模式
python preflight.py --full   # 完整模式
python preflight.py --force  # 强制模式
```

**智能模式优势**：
- 邀请码完整（3个）→ 跳过登录
- 邀请码不完整 → 登录检查
- 节省 50%-100% 时间

## 注册流程

1. **邀请码验证** — 从邀请码池取码验证
2. **填写信息** — 邮箱 + 密码
3. **邮箱验证码** — Web API → IMAP → 重发
4. **生成邀请码** — 登录后生成 3 个邀请码
5. **邀请码分配** — 1 个回池，2 个输出

### 关键规则

- **邀请码永不丢弃**：失败时放回池中
- **自动补充邀请码**：失效时从输出池补充
- **上下文隔离**：每个账号独立浏览器上下文
- **代理自动切换**：遇到 429 限流自动切换节点并重启浏览器

## 异常处理

### 邀请码失效
- 自动从输出池补充
- 输出池为空时自动登录关联账号生成新码

### 邮箱已注册
- 自动登录验证邀请码状态
- 邀请码放回池中继续使用

### 请求限流（429）
- 标记代理失败
- Mihomo 自动切换节点
- 关闭并重启浏览器应用新代理

## 输出文件

### state.json - 运行状态（数据源）
```json
{
  "version": "2.0",
  "invite_pool": ["CODE1"],           // 可用邀请码
  "output_codes": ["CODE2", "..."],   // 已产出邀请码
  "accounts": {
    "email@outlook.com": {
      "status": "completed",
      "password": "xxx",
      "invite_code_used": "CODE1",
      "invite_codes_generated": ["A", "B", "C"],
      "codes_generation_complete": true,  // 邀请码是否完整
      "timestamp": "2026-02-24 04:49:00"
    }
  }
}
```

### registration_report.csv - 注册报告
- 所有账号详情（邮箱、密码、状态、邀请码）
- 邀请码汇总（可用/已使用）
- 统计信息
- 可用 Excel 打开

## 辅助工具

### manual_generate_codes.py - 手动生成邀请码
```bash
python manual_generate_codes.py <email> <password>
```

用于邀请码池耗尽时补充或验证账号状态。

## 常见问题

**Q: 邀请码池为空？**
A: 程序会自动从输出池补充，或提示运行 manual_generate_codes.py

**Q: 验证码收不到？**
A: Web API 3次重试 → IMAP 轮询 30秒 → 重发 → 再轮询 30秒

**Q: EvoMap 服务 502？**
A: 等待恢复后重新运行，state.json 保存进度不丢失

**Q: 代理被限流？**
A: Mihomo 会自动切换节点并重启浏览器

## 最佳实践

1. **首次运行**：使用智能预检模式
2. **定期备份**：备份 state.json
3. **监控进度**：观察浏览器窗口
4. **查看结果**：用 Excel 打开 registration_report.csv
5. **代理配置**：使用 Mihomo 代理池实现自动节点切换
