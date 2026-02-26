# 代理配置指南

## 代理方式对比

| 方式 | 配置难度 | 适用场景 | 推荐度 |
|------|---------|---------|--------|
| **免费代理自动抓取** | ⭐ 零配置 | 快速上手、测试环境 | ⭐⭐⭐ 入门推荐 |
| **常规代理文件** | ⭐ 简单 | 有固定代理服务商 | ⭐⭐⭐⭐⭐ 生产推荐 |
| **Mihomo 代理** | ⭐⭐ 中等 | 已使用 Mihomo 科学上网 | ⭐⭐⭐ 备选 |

**推荐**: 生产环境优先使用常规代理池（付费代理质量更高）。快速测试可用免费代理自动抓取。

---

## 代理文件架构

项目使用两个独立的代理文件，运行时自动合并去重加载到统一代理池：

| 文件 | 用途 | 管理方式 |
|------|------|---------|
| `data/free_proxies.txt` | 自动抓取的免费代理 | 脚本自动生成，每次抓取覆盖 |
| `data/proxies.txt` | 手动维护的付费/自建代理 | 用户手动编辑，不会被覆盖 |

两个文件格式相同，每行一个代理地址：
```
http://1.2.3.4:8080
socks5://5.6.7.8:1080
```

无论选择"免费代理"还是"常规代理"模式，系统都会通过 `ProxyPool.from_files()` 将两个文件合并加载，自动去重。区别仅在于是否先执行自动抓取。

---

## 方式一：免费代理自动抓取（默认）

### 工作原理

从 [free-proxy-list.net](https://free-proxy-list.net/) 自动抓取免费 HTTPS 代理，验证后保存到 `data/free_proxies.txt`，再与 `data/proxies.txt` 合并加载。

### 快速开始

```bash
python start.py
# 步骤 2 选择 "1. 自动抓取免费代理"
# 系统自动抓取 → 验证 → 保存到 data/free_proxies.txt → 合并加载代理池
```

### 代码中使用

```python
from proxy_pool import ProxyPool

# 合并加载两个代理文件
pool = ProxyPool.from_files([
    "data/free_proxies.txt",
    "data/proxies.txt"
], strategy="random")

proxy = pool.get_proxy()
pool.mark_success(proxy)
pool.mark_failed(proxy)
```

### 独立使用抓取模块

```python
from free_proxy_fetcher import FreeProxyFetcher, fetch_and_save

# 一键抓取并保存（不影响 proxies.txt）
proxies = fetch_and_save("data/free_proxies.txt", min_count=5)

# 分步操作
fetcher = FreeProxyFetcher()
fetcher.fetch()
fetcher.validate()
fetcher.save_to_file("data/free_proxies.txt")
```

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `validate_url` | `https://www.google.com` | 验证代理可用性的目标 URL |
| `validate_timeout` | `10` 秒 | 验证超时时间 |
| `max_workers` | `20` | 并发验证线程数 |
| `min_count` | `3` | 最少需要的可用代理数量 |

### 注意事项

- 免费代理不稳定，可能随时失效、速度慢、被封禁
- 存在安全风险（流量监听、数据注入），仅用于测试
- 数量有限（通常 5-20 个），不适合大规模并发
- 抓取源网站需要能访问外网

---

## 方式二：常规代理文件（生产推荐）

### 快速开始

创建 `data/proxies.txt`，每行一个代理：
```
http://1.2.3.4:8080
http://5.6.7.8:8080
socks5://9.10.11.12:1080
```

```bash
python start.py
# 步骤 2 选择 "2. 使用已有代理文件"
# 系统合并加载 proxies.txt + free_proxies.txt（如果存在）
```

### 代码中使用

```python
from proxy_pool import ProxyPool

# 从单个文件加载
pool = ProxyPool.from_file("data/proxies.txt", strategy="random")

# 从多个文件合并加载（推荐）
pool = ProxyPool.from_files([
    "data/proxies.txt",
    "data/free_proxies.txt"
], strategy="random")

proxy = pool.get_proxy()
pool.mark_success(proxy)
pool.mark_failed(proxy)
```

### 代理来源

**购买代理服务**（推荐）：
- 住宅代理: Bright Data, Smartproxy, Oxylabs
- 数据中心代理: ProxyRack, Proxy-Cheap, Webshare
- SOCKS5 代理: 922S5, IPRoyal

### 轮换策略

```python
pool = ProxyPool.from_file("data/proxies.txt", strategy="random")       # 随机（默认）
pool = ProxyPool.from_file("data/proxies.txt", strategy="sequential")   # 顺序轮换
pool = ProxyPool.from_file("data/proxies.txt", strategy="least_used")   # 最少使用
```

### 失败处理

```python
pool = ProxyPool.from_file("data/proxies.txt", max_failures=3)      # 失败3次自动禁用
pool = ProxyPool.from_file("data/proxies.txt", retry_interval=300)   # 失败后重试间隔(秒)
```

---

## 方式三：Mihomo 代理

### 前提条件

- 已安装并运行 Mihomo
- RESTful API 已启用（默认端口 9090）
- 知道 API 密钥（如果有）

### 配置文件方式（推荐）

复制模板并编辑：
```bash
cp data-templates/mihomo.example.json data/mihomo.json
```

`data/mihomo.json`：
```json
{
  "enabled": true,
  "control_url": "http://192.168.100.1:9090",
  "secret": "your_secret",
  "proxy_group": "全部节点",
  "proxy_port": 7890,
  "strategy": "random"
}
```

参数说明：
- `control_url`: Mihomo API 地址（本地 `127.0.0.1` 或远程 IP）
- `secret`: API 密钥
- `proxy_group`: 代理组名称
- `proxy_port`: 代理端口（默认 7890）
- `strategy`: 切换策略（random/sequential/least_used）

### 代码方式

```python
from proxy_pool import ProxyPool

# 本地 Mihomo
pool = ProxyPool.from_mihomo_local(
    control_url="http://127.0.0.1:9090",
    secret="",
    proxy_group="PROXY"
)

# 远程 Mihomo
pool = ProxyPool.from_mihomo_remote(
    control_url="http://192.168.100.1:9090",
    secret="your_secret",
    proxy_group="全部节点"
)

proxy = pool.get_proxy()    # 返回 http://127.0.0.1:7890
pool.mark_failed(proxy)     # 自动切换节点
```

### Mihomo 配置示例

```yaml
# ~/.config/mihomo/config.yaml
external-controller: 0.0.0.0:9090
secret: "your_secret"
port: 7890
socks-port: 7891

proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - 节点1
      - 节点2
      - 节点3
```

### 注意事项

- 通过 API 切换节点会影响所有使用该代理的应用
- 确保 9090（API）和 7890（代理）端口未被占用
- 远程 Mihomo 需要防火墙放行这些端口

---

## 常见问题

**Q: 代理连接失败？**
A: 检查代理地址格式、端口是否正确、防火墙设置

**Q: 免费代理抓取不到？**
A: 确保能访问 free-proxy-list.net，可能需要先配置系统代理

**Q: 代理被限流（429）？**
A: 系统会自动标记失败并切换到下一个代理/节点

**Q: 两个代理文件如何协作？**
A: `free_proxies.txt`（自动抓取）和 `proxies.txt`（手动维护）在运行时通过 `ProxyPool.from_files()` 合并去重加载，互不干扰

**Q: Mihomo 节点不切换？**
A: 需要失败 3 次才会触发切换（可通过 `max_failures` 参数调整）
