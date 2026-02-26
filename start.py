#!/usr/bin/env python3
"""
自动注册工具集 - 统一启动入口

功能：
1. 检查数据文件是否存在
2. 配置代理方式（自动抓取免费代理 / 已有代理文件 / Mihomo / 不使用）
3. 引导用户选择项目（EvoMap / ChatGPT）
4. 自动初始化配置
5. 启动注册流程
"""

import os
import sys
import subprocess
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()

# 将 common 目录加入 sys.path
sys.path.insert(0, str(PROJECT_ROOT / "common"))

# 数据文件路径
EMAIL_FILE = PROJECT_ROOT / "data" / "outlook令牌号.csv"
EMAIL_EXAMPLE = PROJECT_ROOT / "data-templates" / "outlook令牌号.example.csv"
PROXY_FILE = PROJECT_ROOT / "data" / "proxies.txt"
FREE_PROXY_FILE = PROJECT_ROOT / "data" / "free_proxies.txt"
PROXY_EXAMPLE = PROJECT_ROOT / "data-templates" / "proxies.example.txt"
MIHOMO_CONFIG = PROJECT_ROOT / "data" / "mihomo.json"

# 项目配置
EVOMAP_STATE = PROJECT_ROOT / "projects" / "evomap" / "output" / "state.json"
EVOMAP_STATE_EXAMPLE = PROJECT_ROOT / "projects" / "evomap" / "output" / "state.example.json"


def print_header(text):
    """打印标题"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def check_data_files():
    """检查数据文件是否存在"""
    print_header("步骤 1: 检查数据文件")

    missing = []

    # 检查邮箱文件
    if not EMAIL_FILE.exists():
        print(f"[缺失] 邮箱资源池: {EMAIL_FILE}")
        missing.append("email")
    else:
        print(f"[OK] 邮箱资源池: {EMAIL_FILE}")

    # 检查代理文件（可选）
    if not PROXY_FILE.exists():
        print(f"[可选] 代理列表: {PROXY_FILE} (未配置)")
    else:
        print(f"[OK] 代理列表: {PROXY_FILE}")

    # 检查 Mihomo 配置（可选）
    if MIHOMO_CONFIG.exists():
        print(f"[OK] Mihomo 配置: {MIHOMO_CONFIG}")
    else:
        print(f"[可选] Mihomo 配置: {MIHOMO_CONFIG} (未配置)")

    return missing


def setup_email_file():
    """引导用户设置邮箱文件"""
    print("\n邮箱资源池文件不存在，需要创建。")
    print(f"示例文件: {EMAIL_EXAMPLE}")
    print(f"目标文件: {EMAIL_FILE}")

    choice = input("\n选择操作:\n  1. 复制示例文件并手动编辑\n  2. 退出，稍后手动创建\n请选择 (1/2): ").strip()

    if choice == "1":
        # 确保目录存在
        EMAIL_FILE.parent.mkdir(parents=True, exist_ok=True)

        # 复制示例文件
        import shutil
        shutil.copy(EMAIL_EXAMPLE, EMAIL_FILE)
        print(f"\n已复制示例文件到: {EMAIL_FILE}")
        print("\n请编辑此文件，填入真实邮箱信息（格式：邮箱----密码----client_id----refresh_token）")

        input("\n编辑完成后按回车继续...")

        if not EMAIL_FILE.exists() or EMAIL_FILE.stat().st_size < 100:
            print("\n[错误] 文件未正确配置，请检查后重新运行")
            sys.exit(1)
    else:
        print("\n请手动创建邮箱文件后重新运行此脚本")
        sys.exit(0)


def configure_proxy():
    """配置代理方式，返回 proxy_mode 字符串"""
    print_header("步骤 2: 配置代理")

    # 检测已有配置
    has_proxy_file = PROXY_FILE.exists() and PROXY_FILE.stat().st_size > 10
    has_free_proxy_file = FREE_PROXY_FILE.exists() and FREE_PROXY_FILE.stat().st_size > 10
    has_mihomo = MIHOMO_CONFIG.exists()

    print("\n代理方式:")
    print("  1. 自动抓取免费代理（默认，从 free-proxy-list.net 抓取）")
    if has_proxy_file or has_free_proxy_file:
        extra = []
        if has_proxy_file:
            extra.append("proxies.txt")
        if has_free_proxy_file:
            extra.append("free_proxies.txt")
        print(f"     已有代理文件 ({', '.join(extra)}) 会一并加入代理池")
    print("  2. 使用已有代理文件" + (f" ({PROXY_FILE})" if has_proxy_file else " (需先配置 data/proxies.txt)"))
    print("  3. Mihomo 代理" + (" (已检测到配置)" if has_mihomo else " (需先配置 data/mihomo.json)"))
    print("  4. 不使用代理")

    choice = input("\n请选择 (1/2/3/4，默认 1): ").strip() or "1"

    if choice == "1":
        print("\n[Info] 将自动抓取免费代理并生成代理池...")
        print("[Info] 抓取过程需要访问外网，请确保网络畅通")

        try:
            from free_proxy_fetcher import fetch_and_save
            proxy_file = str(FREE_PROXY_FILE)
            proxies = fetch_and_save(proxy_file, min_count=3)

            if proxies:
                print(f"\n[OK] 已获取 {len(proxies)} 个可用代理，保存到 {FREE_PROXY_FILE}")
                return "free_proxy"
            else:
                print("\n[警告] 未能获取到可用代理")
                fallback = input("是否继续（不使用代理）? (Y/n): ").strip().lower()
                if fallback == "n":
                    sys.exit(0)
                return "none"
        except Exception as e:
            print(f"\n[错误] 自动抓取代理失败: {e}")
            fallback = input("是否继续（不使用代理）? (Y/n): ").strip().lower()
            if fallback == "n":
                sys.exit(0)
            return "none"

    elif choice == "2":
        if not has_proxy_file:
            print(f"\n[错误] 代理文件不存在: {PROXY_FILE}")
            print(f"请参考模板创建: {PROXY_EXAMPLE}")
            sys.exit(1)
        print(f"\n[OK] 使用代理文件: {PROXY_FILE}")
        if has_free_proxy_file:
            print(f"[OK] 同时加载免费代理: {FREE_PROXY_FILE}")
        return "file"

    elif choice == "3":
        if not has_mihomo:
            print(f"\n[错误] Mihomo 配置不存在: {MIHOMO_CONFIG}")
            print("请参考模板创建: data-templates/mihomo.example.json")
            sys.exit(1)
        print(f"\n[OK] 使用 Mihomo 代理")
        return "mihomo"

    else:
        print("\n[Info] 不使用代理")
        return "none"


def check_evomap_state():
    """检查 EvoMap state.json 是否存在"""
    if not EVOMAP_STATE.exists():
        print("\n[初始化] EvoMap state.json 不存在，需要初始化")

        # 询问初始邀请码
        invite_code = input("\n请输入初始邀请码（8位大写字母+数字，如 ABCD1234）: ").strip().upper()

        if len(invite_code) != 8:
            print("[警告] 邀请码格式可能不正确，但仍会继续")

        # 创建初始 state.json
        import json
        state = {
            "version": "2.0",
            "invite_pool": [invite_code] if invite_code else [],
            "output_codes": [],
            "accounts": {},
            "invite_codes_history": {}
        }

        EVOMAP_STATE.parent.mkdir(parents=True, exist_ok=True)
        with open(EVOMAP_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        print(f"[OK] 已创建 state.json，初始邀请码: {invite_code}")
    else:
        print(f"[OK] EvoMap state.json 已存在")


def select_project():
    """选择要运行的项目"""
    print_header("步骤 3: 选择项目")

    print("\n可用项目:")
    print("  1. EvoMap - 邀请码裂变注册（需要初始邀请码）")
    print("  2. ChatGPT - 批量并发注册")
    print("  3. 退出")

    choice = input("\n请选择 (1/2/3): ").strip()

    if choice == "1":
        return "evomap"
    elif choice == "2":
        return "chatgpt"
    else:
        print("\n已退出")
        sys.exit(0)


def _build_proxy_args(proxy_mode):
    """根据代理模式构建传递给子项目的命令行参数"""
    return ["--proxy-mode", proxy_mode]


def run_evomap(proxy_mode):
    """运行 EvoMap 项目"""
    print_header("启动 EvoMap 注册")

    # 检查 state.json
    check_evomap_state()

    # 询问预检模式
    print("\nEvoMap 预检模式:")
    print("  1. 智能模式 - 只检查邀请码不完整的账号（推荐，快速）")
    print("  2. 跳过预检 - 完全信任 state.json，直接注册")
    print("  3. 完整预检 - 检查所有已注册账号（慢，全面）")
    print("  4. 强制验证 - 忽略 state.json，登录所有邮箱验证（最慢，最全面）")
    print("  5. 直接注册 - 不运行预检，直接开始注册")

    mode = input("\n请选择 (1/2/3/4/5): ").strip()

    os.chdir(PROJECT_ROOT / "projects" / "evomap")

    proxy_args = _build_proxy_args(proxy_mode)

    if mode == "2":
        print("\n跳过预检，启动注册流程...")
        subprocess.run([sys.executable, "preflight.py", "--skip"] + proxy_args)
    elif mode == "3":
        print("\n启动完整预检流程...")
        subprocess.run([sys.executable, "preflight.py", "--full"] + proxy_args)
    elif mode == "4":
        print("\n启动强制验证流程（忽略 state.json）...")
        subprocess.run([sys.executable, "preflight.py", "--force"] + proxy_args)
    elif mode == "5":
        print("\n直接启动注册流程...")
        subprocess.run([sys.executable, "register.py", "--auto"] + proxy_args)
    else:
        print("\n启动智能预检流程...")
        subprocess.run([sys.executable, "preflight.py", "--smart"] + proxy_args)


def run_chatgpt(proxy_mode):
    """运行 ChatGPT 项目"""
    print_header("启动 ChatGPT 注册")

    os.chdir(PROJECT_ROOT / "projects" / "chatgpt")

    proxy_args = _build_proxy_args(proxy_mode)

    print("\n启动注册流程...")
    subprocess.run([sys.executable, "register.py"] + proxy_args)


def main():
    """主流程"""
    print_header("自动注册工具集 - 启动向导")

    # 1. 检查数据文件
    missing = check_data_files()

    if "email" in missing:
        setup_email_file()

    # 2. 配置代理
    proxy_mode = configure_proxy()

    # 3. 选择项目
    project = select_project()

    # 4. 运行项目
    if project == "evomap":
        run_evomap(proxy_mode)
    elif project == "chatgpt":
        run_chatgpt(proxy_mode)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断，已退出")
        sys.exit(0)
    except Exception as e:
        print(f"\n[错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
