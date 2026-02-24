# 数据文件模板

此目录包含数据文件的示例模板，用于帮助用户创建真实的数据文件。

## 文件说明

### outlook令牌号.example.csv
邮箱资源池模板，复制到 `data/outlook令牌号.csv` 后填入真实数据。

**格式**：`邮箱----密码----client_id----refresh_token`

### proxies.example.txt
代理列表模板，复制到 `data/proxies.txt` 后填入真实代理地址。

**格式**：每行一个代理地址，支持 HTTP/HTTPS/SOCKS5 协议。

### mihomo.example.json
Mihomo 代理池配置模板，复制到 `data/mihomo.json` 后填入真实配置。

**格式**：JSON 配置文件

**参数说明**：
- `enabled`: 是否启用 Mihomo 代理池（true/false）
- `control_url`: Mihomo API 地址
- `secret`: Mihomo API 密钥
- `proxy_group`: 代理组名称
- `proxy_port`: Mihomo 代理端口（默认 7890）
- `strategy`: 节点切换策略
  - `random`（随机）- 默认
  - `sequential`（顺序）
  - `least_used`（最少使用）

## 使用方法

```bash
# 复制模板
cp data-templates/outlook令牌号.example.csv data/outlook令牌号.csv
cp data-templates/proxies.example.txt data/proxies.txt
cp data-templates/mihomo.example.json data/mihomo.json

# 编辑文件，填入真实数据
# 然后运行 python start.py
```

## 注意事项

- 真实数据文件（`data/outlook令牌号.csv`, `data/proxies.txt`, `data/mihomo.json`）已在 `.gitignore` 中配置，不会被提交
- 模板文件（`.example` 后缀）会被提交到代码仓库
