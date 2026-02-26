# ChatGPT 批量自动注册工具

使用 Outlook 邮箱批量自动注册 ChatGPT 账号，支持并发注册和自动获取 OTP 验证码。

## 依赖安装

```bash
pip install curl_cffi
```

## 快速开始

### 首次使用初始化

1. **准备邮箱资源池**
   ```bash
   # 使用共享的邮箱资源池
   # 文件位置: ../data/outlook令牌号.csv
   # 格式: email----password----client_id----refresh_token
   ```

2. **配置代理（推荐）**

   ChatGPT 注册通常需要代理才能访问 OpenAI 服务。支持三种方式：

   **方式一：环境变量（推荐）**
   ```bash
   # Windows
   set HTTPS_PROXY=http://127.0.0.1:7890

   # Linux/Mac
   export HTTPS_PROXY=http://127.0.0.1:7890
   ```

   **方式二：运行时输入**
   ```bash
   python register.py
   # 脚本会自动检测环境变量，也可手动输入
   ```

   **注意**：代理轮换功能已集成，详见 [../../common/PROXY_GUIDE.md](../../common/PROXY_GUIDE.md)。

### 运行注册

```bash
python register.py
```

启动后会依次交互式提示：

1. **代理配置** — 自动检测环境变量 `HTTPS_PROXY` / `ALL_PROXY`，也可手动输入或留空跳过
2. **邮件获取方式** — 选择 IMAP（默认）或 Graph API
3. **邮箱文件路径** — 默认使用 `../data/outlook令牌号.csv`
4. **并发数** — 默认 3（建议 3-5，过高可能触发风控）

## 邮箱文件格式

使用共享的邮箱资源池 `../data/outlook令牌号.csv`，格式如下：

```
email----password----client_id----refresh_token
```

| 字段 | 说明 |
|------|------|
| `email` | Outlook 邮箱地址 |
| `password` | Outlook 邮箱密码（用于记录，不参与注册流程） |
| `client_id` | Microsoft OAuth 应用的 Client ID |
| `refresh_token` | Microsoft OAuth 的 Refresh Token |

### refresh_token scope 说明

`refresh_token` 的获取方式决定了邮件读取方式：

- **IMAP scope**：授权时包含 `https://outlook.office.com/IMAP.AccessAsUser.All` scope，使用 IMAP + XOAUTH2 协议读取邮件
- **Graph scope**：授权时包含 `https://graph.microsoft.com/Mail.Read` 等 Graph API scope，使用 Microsoft Graph API 读取邮件

两种 scope 的 `refresh_token` 格式相同，但需要在启动工具时选择对应的邮件获取方式。

示例：

```
user1@outlook.com----outlookpwd1----xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx----M.C5xxxxx
user2@outlook.com----outlookpwd2----xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx----M.C5xxxxx
```

以 `#` 开头的行会被跳过。

**示例文件**：参见 [../data/examples/outlook令牌号.example.csv](../data/examples/outlook令牌号.example.csv)

## 邮件获取方式

| 方式 | 适用场景 | 说明 |
|------|----------|------|
| IMAP（默认） | refresh_token 授权了 IMAP scope | 通过 IMAP + XOAUTH2 协议直接读取邮件 |
| Graph API | refresh_token 授权了 Graph scope | 通过 Microsoft Graph REST API 读取邮件 |

如果使用 IMAP 方式获取 token 失败，可以尝试切换到 Graph API 方式。

## 输出

注册成功的账号会追加写入 `registered_accounts.txt`，格式：

```
email----chatgpt_password
```

其中 `chatgpt_password` 是工具自动生成的 ChatGPT 账号密码。

## 注意事项

- 需要能访问 OpenAI 服务的网络环境，通常需要配置代理
- 并发数建议 3-5，过高可能触发风控
- 每个邮箱的 `client_id` 和 `refresh_token` 需要提前通过 Microsoft OAuth 流程获取
- OTP 验证码自动从 Outlook 收件箱读取，超时时间为 120 秒
- Graph API 方式无需额外依赖，复用 curl_cffi 发送 HTTP 请求
