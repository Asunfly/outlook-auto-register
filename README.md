# Outlook 邮箱自动注册工具集

基于 Outlook 邮箱 OAuth2 认证的批量自动注册工具集，支持多个目标平台共享同一套邮箱接码模块。

## 快速开始

### 一键启动（推荐）

```bash
python start.py
```

启动向导会自动：
1. 检查数据文件是否存在
2. 引导创建邮箱资源池
3. 配置代理方式（自动抓取免费代理 / 已有代理文件 / Mihomo / 手动输入）
4. 选择项目（EvoMap / ChatGPT）
5. 初始化配置（如 EvoMap 初始邀请码）
6. 启动注册流程

### 手动启动

如果你已经配置好所有文件，也可以直接运行：

```bash
# EvoMap: 预检 + 批量注册
cd evomap
python preflight.py

# ChatGPT: 直接注册
cd chatgpt
python register.py
```

### 安装依赖

```bash
# EvoMap 项目
pip install playwright requests
playwright install chromium

# ChatGPT 项目
pip install curl_cffi requests
```

## 目录结构

```
微软mail/
├── start.py                     # 统一启动入口
├── README.md                    # 项目说明
├── .gitignore                   # Git 忽略配置
│
├── data/                        # 数据目录（真实数据，已忽略）
│   ├── outlook令牌号.csv        # 邮箱资源池
│   ├── proxies.txt              # 手动维护的代理列表（可选）
│   └── free_proxies.txt         # 自动抓取的免费代理（可选）
│
├── data-templates/              # 数据文件模板（会提交）
│   ├── README.md                # 模板说明
│   ├── outlook令牌号.example.csv  # 邮箱格式示例
│   └── proxies.example.txt      # 代理格式示例
│
├── common/                      # 共享模块
│   ├── outlook_mail.py          # 邮箱接码模块
│   ├── proxy_pool.py            # 代理池模块
│   ├── free_proxy_fetcher.py    # 免费代理自动抓取模块
│   └── PROXY_GUIDE.md           # 代理配置完整指南
│
├── projects/                    # 注册项目
│   ├── evomap/                  # EvoMap 注册项目
│   │   ├── register.py          # 批量注册主脚本
│   │   ├── preflight.py         # 智能预检工具
│   │   ├── manual_generate_codes.py  # 手动生成邀请码
│   │   ├── 使用说明.md
│   │   ├── SMART_PREFLIGHT.md   # 智能预检说明
│   │   └── output/              # 输出目录（已忽略）
│   │       ├── state.json       # 运行状态
│   │       ├── state.example.json  # 状态文件模板
│   │       └── registration_report.csv  # 注册报告
│   │
│   └── chatgpt/                 # ChatGPT 注册项目
│       ├── register.py          # 并发注册主脚本
│       ├── README.md
│       └── output/              # 输出目录（已忽略）
│
├── dev-archive/                 # 开发归档（已忽略）
│   ├── STATE_MANAGEMENT_OPTIMIZATION_V2.md  # 状态管理优化方案
│   └── examples/                # 代码示例（已归档）
│
└── 参考文件/                    # 原始参考脚本（已忽略）
```

## 核心模块

### outlook_mail.py - 邮箱接码模块

统一的 Outlook 邮箱收信模块，支持三种收信通道（按优先级）:

| 通道 | 说明 | 适用场景 |
|------|------|---------|
| Web API | 第三方接口，速度最快 | 有可用的 Web API 服务时优先使用 |
| IMAP | OAuth2 XOAUTH2 认证 | 标准方式，稳定可靠 |
| Graph API | Microsoft Graph | IMAP 被限制时的备选方案 |

### 设计原则

- **只负责收信**: 连接邮箱 → 搜索/获取邮件 → 返回原始内容
- **业务解耦**: 验证码提取逻辑由调用方通过 `code_extractor` 回调函数传入
- **Web API 可选**: 通过 `web_api_url` 参数控制是否启用，不传则不启用

### 使用方式

```python
from outlook_mail import OutlookMailClient

# 1. 业务方定义自己的验证码提取函数
def my_extractor(subject, body, sender):
    import re
    m = re.search(r'Your code is (\d{6})', subject)
    return m.group(1) if m else None

# 2. 创建客户端
client = OutlookMailClient(
    email="user@outlook.com",
    client_id="xxx",
    refresh_token="xxx",
    sender_filter="noreply@example.com",   # 发件人过滤
    code_extractor=my_extractor,            # 业务方定义的提取函数
    web_api_url="http://xxx/api/search",    # 可选
)

# 3. 获取已有邮件 ID (基线快照)
known_ids = client.get_known_ids()

# 4. ... 触发目标网站发送验证码 ...

# 5. 轮询新邮件并提取验证码
code = client.poll_for_code(known_ids, timeout=120)
```

### 主要参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `email` | str | Outlook 邮箱地址 |
| `client_id` | str | OAuth2 应用 client_id |
| `refresh_token` | str | OAuth2 refresh_token |
| `sender_filter` | str | 发件人域名过滤 (如 `"openai.com"`) |
| `code_extractor` | callable | `(subject, body, sender) -> str/None` 回调函数 |
| `web_api_url` | str/None | Web API 地址，不传则不启用 |
| `proxy` | str/None | HTTP 代理地址 |
| `folders` | list | 搜索的邮箱文件夹，默认 `["Junk", "INBOX"]` |
| `use_graph` | bool | 是否使用 Graph API 代替 IMAP，默认 `False` |

### proxy_pool.py - IP 代理池模块

统一的 IP 代理管理模块，支持三种代理方式:

| 方式 | 说明 | 适用场景 | 推荐度 |
|------|------|---------|--------|
| 免费代理自动抓取 | 从 free-proxy-list.net 自动抓取 | 快速上手、测试环境 | ⭐⭐⭐ 入门推荐 |
| 常规代理 | HTTP/HTTPS/SOCKS5 静态列表或 API | 有固定代理服务商，需要轮换多个代理 IP | ⭐⭐⭐⭐⭐ 生产推荐 |
| Mihomo 代理 | 复用宿主机或远程的 Mihomo 服务 | 已使用 Mihomo 科学上网，接受全局切换 | ⭐⭐⭐ 备选 |

**注意**: Mihomo 模式会影响所有使用该代理的应用（全局切换）。如需隔离，建议使用常规代理池。

#### 设计原则

- **只负责代理管理**: 提供可用代理 → 标记失败 → 自动切换（Mihomo）
- **业务解耦**: 具体的代理使用逻辑由调用方决定
- **多种策略**: 支持随机、顺序、最少使用等轮换策略

#### 使用方式

```python
from proxy_pool import ProxyPool

# 方式1: 免费代理自动抓取（零配置，适合测试）
pool = ProxyPool.from_free_proxy(save_path="data/proxies.txt")

# 方式2: 常规代理（推荐，从文件加载）
pool = ProxyPool.from_file("data/proxies.txt")

# 方式3: Mihomo 本地代理（复用宿主机 Mihomo）
pool = ProxyPool.from_mihomo_local(
    control_url="http://127.0.0.1:9090",  # Mihomo API 地址
    secret="",                             # API 密钥
    proxy_group="PROXY"                    # 代理组名称
)

# 方式4: Mihomo 远程代理（复用远程服务器 Mihomo）
pool = ProxyPool.from_mihomo_remote(
    control_url="http://remote-server:9090",
    secret="your_secret",
    proxy_group="PROXY"
)

# 获取代理
proxy = pool.get_proxy()

# 标记失败（Mihomo 模式会自动切换节点）
pool.mark_failed(proxy)

# 标记成功
pool.mark_success(proxy)
```

#### 主要参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `proxies` | list | 常规代理列表 |
| `api_url` | str/None | 代理 API 地址 |
| `mihomo_controller` | MihomoController | Mihomo 控制器实例 |
| `mihomo_group` | str | Mihomo 代理组名称 |
| `strategy` | str | 轮换策略 (random/sequential/least_used) |
| `max_failures` | int | 最大失败次数，默认 3 |
| `auto_switch` | bool | 失败时自动切换节点（仅 Mihomo），默认 True |

**完整代理配置指南**: 参见 [common/PROXY_GUIDE.md](common/PROXY_GUIDE.md) （免费代理、常规代理、Mihomo 代理）

## 数据文件配置

### 邮箱资源池

`data/outlook令牌号.csv` — 所有项目共享的邮箱账号池。

**格式**: `邮箱----密码----client_id----refresh_token` (以 `----` 分隔)，首行为表头。

**示例文件**: [data-templates/outlook令牌号.example.csv](data-templates/outlook令牌号.example.csv)

### 代理资源池

#### 方式 1：常规代理（推荐）

`data/proxies.txt` — 所有项目共享的代理列表（可选）。

**格式**: 每行一个代理地址，支持 HTTP/HTTPS/SOCKS5 协议。

**示例文件**: [data-templates/proxies.example.txt](data-templates/proxies.example.txt)

#### 方式 2：Mihomo 代理池（支持节点切换）

`data/mihomo.json` — Mihomo 代理配置文件（可选）。

**格式**: JSON 配置文件，包含 API 地址、密钥、代理组等信息。

**示例文件**: [data-templates/mihomo.example.json](data-templates/mihomo.example.json)

**配置示例**：
```json
{
  "enabled": true,
  "control_url": "http://192.168.100.1:9090",
  "secret": "123456",
  "proxy_group": "🌐 全部节点",
  "proxy_port": 7890,
  "strategy": "random"
}
```

**配置参数说明**：
- `enabled`: 是否启用 Mihomo 代理池（true/false）
- `control_url`: Mihomo API 地址
- `secret`: Mihomo API 密钥（如果没有设置密钥，留空字符串）
- `proxy_group`: 代理组名称（从 Mihomo 配置中获取）
- `proxy_port`: Mihomo 代理端口（默认 7890）
- `strategy`: 节点切换策略，可选值：
  - `random`（随机）- 默认，从可用节点中随机选择
  - `sequential`（顺序）- 按顺序轮换节点
  - `least_used`（最少使用）- 选择最久未使用的节点

**使用说明**：
1. 复制示例文件到 `data/mihomo.json`
2. 修改配置（API 地址、密钥、代理组名称）
3. 设置 `"enabled": true` 启用
4. 运行注册脚本时会自动使用 Mihomo 代理池
5. 遇到 429/限流时自动切换节点并重启浏览器

**注意**: Mihomo 的 vless/vmess/reality 等协议会通过 Mihomo 本地代理端口（默认 7890）转换为 HTTP 代理，通过 RESTful API（默认 9090）控制节点切换。

### 代理集成说明

**EvoMap 项目**：已集成 Mihomo 代理池，支持自动节点切换和浏览器重启。

**ChatGPT 项目**：目前使用单个固定代理（通过环境变量或运行时输入）。

### 数据安全

⚠️ **重要提示**：
- 真实数据文件（`outlook令牌号.csv`, `proxies.txt`）已在 `.gitignore` 中配置，不会被提交到代码仓库
- 输出目录（`projects/evomap/output/`, `projects/chatgpt/output/`）也已忽略，避免泄露注册账号信息
- 使用示例文件（`.example` 后缀）作为模板，复制后填入真实数据
- 开发归档目录（`dev-archive/`）和参考文件（`参考文件/`）已忽略，不会被提交

## 项目说明

### EvoMap 注册

使用 Playwright 浏览器自动化完成 EvoMap 网站注册，支持邀请码裂变。

```bash
# 安装依赖
pip install playwright requests
playwright install chromium

# 预检: 检查邮箱可用性
python projects/evomap/preflight.py

# 批量注册
python projects/evomap/register.py
```

**依赖**: `pip install playwright requests` + `playwright install chromium`

**特点**:
- Playwright 驱动 Chromium 浏览器自动化（自动等待、隔离上下文）
- 注册后自动生成邀请码并回填注册池
- 支持断点续跑 (state.json)
- Web API + IMAP 双通道接码

### ChatGPT 注册

使用 curl_cffi 进行反指纹 HTTP 请求，并发批量注册 ChatGPT 账号。

```bash
python projects/chatgpt/register.py --help
```

**依赖**: `pip install curl_cffi requests`

**特点**:
- curl_cffi 模拟真实浏览器指纹 (Chrome 131/133/136/142)
- 多线程并发注册
- IMAP 通道接码 (不使用 Web API)
- 自动处理 Arkose/Turnstile 验证

## 环境要求

- Python 3.8+
- 各项目依赖见对应说明
- Outlook 邮箱需要已配置 OAuth2 应用并获取 refresh_token

## 文档

- [代理配置指南](common/PROXY_GUIDE.md) - 免费代理自动抓取、常规代理和 Mihomo 代理完整配置说明
- [EvoMap 使用说明](projects/evomap/README.md) - EvoMap 项目详细文档
- [ChatGPT 使用说明](projects/chatgpt/README.md) - ChatGPT 项目详细文档
