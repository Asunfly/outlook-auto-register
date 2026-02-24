# 提交前检查清单

## ✅ 敏感信息检查

### 已忽略的敏感文件
- [x] `data/outlook令牌号.csv` - 邮箱账号信息
- [x] `data/proxies.txt` - 代理列表
- [x] `data/mihomo.json` - Mihomo 配置
- [x] `projects/evomap/output/*.csv` - 注册报告
- [x] `projects/evomap/output/*.json` - 运行状态（除 .example.json）
- [x] `projects/chatgpt/output/` - ChatGPT 输出
- [x] `dev-archive/` - 开发归档
- [x] `参考文件/` - 参考文件
- [x] `.claude/settings.local.json` - Claude 本地配置

### 将要提交的文件（17个）
- [x] `.gitignore` - Git 忽略配置
- [x] `README.md` - 项目说明
- [x] `start.py` - 统一启动脚本
- [x] `common/outlook_mail.py` - 邮箱模块
- [x] `common/proxy_pool.py` - 代理池模块
- [x] `data-templates/` - 数据模板（4个文件）
- [x] `docs/PROXY_GUIDE.md` - 代理配置指南
- [x] `projects/evomap/` - EvoMap 项目（5个文件）
- [x] `projects/chatgpt/` - ChatGPT 项目（2个文件）

## ✅ 文档完整性检查

- [x] README.md 路径引用正确
- [x] 所有文档链接有效
- [x] 代码示例可运行
- [x] 配置模板完整

## ✅ 代码质量检查

- [x] 无硬编码的敏感信息
- [x] 导入路径正确
- [x] 配置文件化
- [x] 错误处理完善

## 提交命令

```bash
# 查看状态
git status

# 添加所有文件
git add -A

# 提交
git commit -m "Initial commit: Outlook 邮箱自动注册工具集

- 支持 EvoMap 和 ChatGPT 批量注册
- 集成 Mihomo 代理池，支持自动节点切换
- 智能预检功能，优化注册流程
- 完整的文档和配置模板"

# 推送到 GitHub
git remote add origin <your-repo-url>
git branch -M main
git push -u origin main
```

## 注意事项

⚠️ **提交前务必确认**：
1. 所有敏感信息已被 .gitignore 忽略
2. 没有硬编码的密码、密钥、邮箱地址
3. 示例文件使用占位符数据
4. 文档中没有真实的配置信息
