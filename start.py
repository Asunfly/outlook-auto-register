#!/usr/bin/env python3
"""
自动注册工具集 - 统一启动入口

功能：
1. 检查数据文件是否存在
2. 引导用户选择项目（EvoMap / ChatGPT）
3. 自动初始化配置
4. 启动注册流程
"""

import os
import sys
import subprocess
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()

# 数据文件路径
EMAIL_FILE = PROJECT_ROOT / "data" / "outlook令牌号.csv"
EMAIL_EXAMPLE = PROJECT_ROOT / "data-templates" / "outlook令牌号.example.csv"
PROXY_FILE = PROJECT_ROOT / "data" / "proxies.txt"
PROXY_EXAMPLE = PROJECT_ROOT / "data-templates" / "proxies.example.txt"

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
    print_header("步骤 2: 选择项目")

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


def run_evomap():
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

    if mode == "2":
        # 跳过预检
        print("\n跳过预检，启动注册流程...")
        subprocess.run([sys.executable, "preflight.py", "--skip"])
    elif mode == "3":
        # 完整预检
        print("\n启动完整预检流程...")
        subprocess.run([sys.executable, "preflight.py", "--full"])
    elif mode == "4":
        # 强制验证
        print("\n启动强制验证流程（忽略 state.json）...")
        subprocess.run([sys.executable, "preflight.py", "--force"])
    elif mode == "5":
        # 直接注册
        print("\n直接启动注册流程...")
        subprocess.run([sys.executable, "register.py"])
    else:
        # 智能预检（默认）
        print("\n启动智能预检流程...")
        subprocess.run([sys.executable, "preflight.py", "--smart"])


def run_chatgpt():
    """运行 ChatGPT 项目"""
    print_header("启动 ChatGPT 注册")

    os.chdir(PROJECT_ROOT / "projects" / "chatgpt")

    print("\n启动注册流程...")
    subprocess.run([sys.executable, "register.py"])


def main():
    """主流程"""
    print_header("自动注册工具集 - 启动向导")

    # 1. 检查数据文件
    missing = check_data_files()

    if "email" in missing:
        setup_email_file()

    # 2. 选择项目
    project = select_project()

    # 3. 运行项目
    if project == "evomap":
        run_evomap()
    elif project == "chatgpt":
        run_chatgpt()


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
