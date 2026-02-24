# 代理配置指南

## 代理方式对比

| 方式 | 配置难度 | 适用场景 | 推荐度 |
|------|---------|---------|--------|
| **常规代理** | ⭐ 简单 | 有固定代理服务商 | ⭐⭐⭐⭐⭐ 推荐 |
| **Mihomo 代理** | ⭐⭐ 中等 | 已使用 Mihomo 科学上网 | ⭐⭐⭐ 备选 |

**推荐**: 优先使用常规代理池，完全隔离不影响其他应用。

---

## 方式一：常规代理（推荐）

### 快速开始

**1. 准备代理列表**

创建 `data/proxies.txt`：
```
http://1.2.3.4:8080
http://5.6.7.8:8080
socks5://9.10.11.12:1080
```

**2. 在代码中使用**

```python
from proxy_pool import ProxyPool

# 创建代理池
pool = ProxyPool.from_file("data/proxies.txt", strategy="random")

# 获取代理
proxy = pool.get_proxy()

# 使用代理发送请求
response = requests.get(url, proxies={"http": proxy, "https": proxy})

# 标记成功/失败
pool.mark_success(proxy)  # 成功
pool.mark_failed(proxy)   # 失败（失败3次后自动禁用）
```

### 代理来源

**购买代理服务**（推荐）：
- 住宅代理: Bright Data, Smartproxy, Oxylabs
- 数据中心代理: ProxyRack, Proxy-Cheap, Webshare
- SOCKS5 代理: 922S5, IPRoyal

**自建代理**（需要 VPS）：
```bash
# 使用 Squid 搭建 HTTP 代理
apt-get install squid
# 配置 /etc/squid/squid.conf
```

### 轮换策略

```python
# 随机选择（默认）
pool = ProxyPool.from_file("data/proxies.txt", strategy="random")

# 顺序轮换
pool = ProxyPool.from_file("data/proxies.txt", strategy="sequential")

# 最少使用
pool = ProxyPool.from_file("data/proxies.txt", strategy="least_used")
```

### 失败处理

```python
# 自动禁用失败代理（默认失败3次）
pool = ProxyPool.from_file("data/proxies.txt", max_failures=3)

# 失败后重试间隔（秒）
pool = ProxyPool.from_file("data/proxies.txt", retry_interval=300)
```

---

## 方式二：Mihomo 代理

### 前提条件

- 已安装并运行 Mihomo
- Mihomo RESTful API 已启用（默认端口 9090）
- 知道 API 密钥（如果有）

### 配置文件方式（推荐）

**1. 创建配置文件**

复制模板：
```bash
cp data-templates/mihomo.example.json data/mihomo.json
```

编辑 `data/mihomo.json`：
```json
{
  "enabled": true,
  "control_url": "http://192.168.100.1:9090",
  "secret": "your_secret",
  "proxy_group": "🌐 全部节点",
  "proxy_port": 7890,
  "strategy": "random"
}
```

**参数说明**：
- `enabled`: 是否启用（true/false）
- `control_url`: Mihomo API 地址
- `secret`: API 密钥
- `proxy_group`: 代理组名称（从 Mihomo 配置获取）
- `proxy_port`: 代理端口（默认 7890）
- `strategy`: 切换策略（random/sequential/least_used）

**2. 运行注册**

配置文件启用后，运行 `python start.py` 会自动使用 Mihomo 代理池。

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
    proxy_group="🌐 全部节点"
)

# 获取代理
proxy = pool.get_proxy()  # 返回 http://127.0.0.1:7890

# 标记失败（自动切换节点）
pool.mark_failed(proxy)
```

### Mihomo 配置示例

确保 Mihomo 配置文件启用了 API：

```yaml
# ~/.config/mihomo/config.yaml

# RESTful API
external-controller: 0.0.0.0:9090  # API 监听地址
secret: "your_secret"               # API 密钥

# 代理端口
port: 7890                          # HTTP 代理端口
socks-port: 7891                    # SOCKS5 代理端口

# 代理组
proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - 节点1
      - 节点2
      - 节点3
```

### 测试连接

```bash
# 测试 API
curl http://127.0.0.1:9090/proxies/PROXY

# 测试代理
curl -x http://127.0.0.1:7890 https://www.google.com
```

### 节点切换策略

- `random`（随机）- 默认，从可用节点中随机选择
- `sequential`（顺序）- 按顺序轮换节点
- `least_used`（最少使用）- 选择最久未使用的节点

### 注意事项

⚠️ **全局切换**：通过 Mihomo API 切换节点会影响所有使用该代理的应用

⚠️ **端口冲突**：确保 9090（API）和 7890（代理）端口未被占用

⚠️ **防火墙**：远程 Mihomo 需要允许访问这些端口

---

## 常见问题

**Q: 代理连接失败？**
A: 检查代理地址格式、端口是否正确、防火墙设置

**Q: 代理被限流（429）？**
A:
- 常规代理：自动切换到下一个代理
- Mihomo：自动切换节点并重启浏览器

**Q: 如何查看代理状态？**
A:
```python
# 查看代理统计
print(pool.proxy_stats)
```

**Q: Mihomo 节点不切换？**
A: 需要失败 3 次才会触发切换（可通过 `max_failures` 参数调整）

**Q: 常规代理和 Mihomo 能同时使用吗？**
A: 不能，只能选择一种方式

---

## 最佳实践

1. **优先使用常规代理**：完全隔离，不影响其他应用
2. **定期更新代理列表**：删除失效代理，添加新代理
3. **监控代理状态**：定期检查 `proxy_stats` 统计信息
4. **合理设置失败阈值**：根据代理质量调整 `max_failures`
5. **Mihomo 用于备选**：已有 Mihomo 环境时可复用
