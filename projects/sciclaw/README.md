# SciClaw 批量自动注册工具

使用 Outlook 邮箱批量自动注册 SciClaw 账号，支持邀请码裂变：

- 用邀请码注册一个新账号
- 自动提取该账号的 3 个邀请码
- 1 个放回 `invite_pool` 继续注册，2 个放入 `output_codes`

## 依赖安装

```bash
pip install playwright requests
playwright install chromium
```

## 准备数据

使用共享邮箱资源池：`../../data/outlook令牌号.csv`

格式：

```text
email----password----client_id----refresh_token
```

## 运行方式

### 自动模式（推荐）

```bash
python register.py --auto --initial-invite SC-B7NDJO7I
```

### 交互模式

```bash
python register.py
```

脚本会提示输入初始邀请码（当 `output/state.json` 中 `invite_pool` 为空时）。

## 状态文件

- `output/state.json`：运行状态（自动生成）
- `output/state.example.json`：状态模板

关键字段：

- `invite_pool`：可继续用于注册的邀请码池
- `output_codes`：额外产出的邀请码
- `accounts`：邮箱注册结果及对应邀请码记录

## 当前流程说明

当前站点验证得到的关键流程为：

1. 首页 `Onboard` 页签输入 `ACCESS CODE`
2. `VERIFY ACCESS CODE` 成功后填写邮箱并 `SEND CODE`
3. Outlook 收码后 `ENTER THE LAB`
4. `DONE` 完成 onboarding，进入 `/chat`
5. 右上角用户菜单 -> `Invite Code`，提取 3 个邀请码
