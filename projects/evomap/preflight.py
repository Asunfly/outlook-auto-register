"""
EvoMap 注册前校验 & 初始化工具
功能: 登录所有已注册账号，校验邀请码真实状态，刷新 state 文件，然后可选启动注册
用法: python preflight.py
"""

import json
import os
import re
import sys
import time
import random
from datetime import datetime

# 将项目根目录的 common 目录加入 sys.path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 复用注册脚本的工具函数
from register import (
    create_browser, create_context, human_type, log,
    load_state, save_state, load_emails, run_batch,
    generate_csv_report,
    BASE_URL, ACCOUNT_URL,
)

LOGIN_URL = f"{BASE_URL}/login"


# ==================== 校验核心函数 ====================

def login_account(page, email, password):
    """用邮箱+密码登录 EvoMap，返回 True/False"""
    page.goto(LOGIN_URL)

    # 等待页面加载
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except PlaywrightTimeout:
        pass
    time.sleep(1)

    # 确保在密码登录模式
    pwd_tab = page.locator("xpath=//button[text()='Password']")
    if pwd_tab.count() > 0 and pwd_tab.first.is_visible():
        pwd_tab.first.click()
        time.sleep(0.5)

    try:
        email_input = page.locator("input[type='text'], input[placeholder='Email']").first
        email_input.wait_for(state="visible", timeout=15000)
        pwd_input = page.locator("input[type='password'], input[placeholder='Password']").first
        pwd_input.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeout:
        log(f"  登录页元素未找到", "ERROR")
        return False

    human_type(email_input, email, delay_range=(30, 80))
    human_type(pwd_input, password, delay_range=(30, 80))

    # 点击登录（服务不稳定时可能需要多次点击）
    login_btn = page.locator(
        "xpath=//button[contains(text(),'Continue') or contains(text(),'Login') or contains(text(),'Sign in')]"
    )

    # 尝试点击 2-3 次，每次间隔 2 秒
    for attempt in range(3):
        try:
            if login_btn.first.is_visible(timeout=5000):
                login_btn.first.click(timeout=5000)
                log(f"  登录按钮已点击 (尝试 {attempt + 1}/3)")
                time.sleep(2)

                # 检查是否已经跳转
                try:
                    page.wait_for_function(
                        "() => !window.location.href.includes('/login')",
                        timeout=3000,
                    )
                    return True  # 跳转成功，直接返回
                except PlaywrightTimeout:
                    if attempt < 2:
                        continue  # 还没跳转，继续重试点击
        except PlaywrightTimeout:
            if attempt == 0:
                log(f"  登录按钮未找到", "ERROR")
                return False
            break

    # 等待跳转离开 /login
    # 先快速检测（15s），失败后再等一轮（45s），总计最多 60s
    for wait_ms in [15000, 45000]:
        try:
            page.wait_for_function(
                "() => !window.location.href.includes('/login')",
                timeout=wait_ms,
            )
            return True
        except PlaywrightTimeout:
            if wait_ms == 15000:
                log(f"  跳转等待 15s 未完成，继续等待...", "WARN")
                continue
            # 最终超时，判断原因
            try:
                body = page.locator("body").inner_text().lower()
                if "invalid" in body or "incorrect" in body or "wrong" in body:
                    log(f"  密码错误", "WARN")
                elif "502" in body or "bad gateway" in body or "503" in body:
                    log(f"  服务不可用(502/503)", "WARN")
                else:
                    log(f"  登录超时(60s)", "WARN")
            except Exception:
                log(f"  登录超时", "WARN")
            return False


def dismiss_onboarding(page, max_steps=10):
    """
    关闭新手引导弹窗（onboarding overlay）。
    新注册账号首次登录会弹出引导，遮住页面内容导致邀请码无法识别。
    尝试多种方式关闭：Skip / Close / Next 按钮、Esc 键、点击遮罩外区域。
    """
    for step in range(max_steps):
        time.sleep(0.5)

        # 尝试点击 Skip / Close / Got it / Finish 等按钮
        for text in ["Skip", "skip", "Close", "close", "Got it", "Finish", "Done", "\u00d7", "\u2715"]:
            btns = page.locator(f"xpath=//button[contains(text(),'{text}')]").all()
            for btn in btns:
                if btn.is_visible():
                    try:
                        btn.click(timeout=2000)
                        log(f"  关闭新手引导 (点击 '{text}')")
                        time.sleep(0.5)
                        break
                    except Exception:
                        pass

        # 尝试点击 aria-label="Close" 或 role="button" 的关闭按钮
        close_btns = page.locator(
            "[aria-label='Close'], [aria-label='close'], "
            "button[class*='close'], button[class*='dismiss'], "
            "[data-dismiss], [data-close]"
        ).all()
        for btn in close_btns:
            if btn.is_visible():
                try:
                    btn.click(timeout=2000)
                    log(f"  关闭新手引导 (aria-label/close 按钮)")
                    time.sleep(0.5)
                    break
                except Exception:
                    pass

        # 检查是否还有遮罩层（overlay / backdrop / modal）
        overlays = page.locator(
            "[class*='overlay'], [class*='backdrop'], [class*='modal'], "
            "[class*='onboarding'], [role='dialog']"
        ).all()
        visible_overlays = [o for o in overlays if o.is_visible()]
        if not visible_overlays:
            return  # 没有遮罩了，退出

        # 尝试按 Esc 键关闭
        page.keyboard.press("Escape")
        time.sleep(0.3)

    log(f"  新手引导可能仍未关闭（尝试了 {max_steps} 步）", "WARN")


def parse_invite_codes(page, context):
    """
    解析 /account 页面的邀请码状态
    返回: {"total": 3, "generated": 3, "available": ["CODE1"], "used": {"CODE2": "user@xxx"}}
    """
    try:
        page.goto(ACCOUNT_URL, timeout=30000)
    except PlaywrightTimeout:
        log(f"  /account 页面加载超时", "WARN")
    time.sleep(3)

    # 关闭可能存在的新手引导弹窗
    dismiss_onboarding(page)

    result = {"total": 0, "generated": 0, "available": [], "used": {}}

    # 精确定位邀请码容器 div
    container = page.locator("div[class*='border-primary'][class*='col-span-2']")
    if container.count() == 0:
        log(f"  未找到邀请码容器 div (border-primary col-span-2)", "WARN")
        container = page.locator("main")
        if container.count() == 0:
            log(f"  未找到 main 元素，页面可能异常", "ERROR")
            return result

    search_root = container.first
    container_text = search_root.inner_text()

    # 在容器内查找 Copy 按钮的前置兄弟元素提取可用码
    copy_buttons = search_root.locator("xpath=.//button[text()='Copy']").all()
    for btn in copy_buttons:
        try:
            code_text = btn.evaluate(
                "el => { var prev = el.previousElementSibling || el.parentElement.previousElementSibling; return prev ? prev.textContent.trim() : ''; }"
            )
            if re.match(r'^[A-F0-9]{8}$', code_text):
                result["available"].append(code_text)
        except Exception:
            pass

    # 用正则在容器文本中搜索所有 8位hex 码
    all_codes = re.findall(r'\b([A-F0-9]{8})\b', container_text)

    # 检测 "used by" 模式：CODE used by email@xxx
    used_pattern = re.findall(r'([A-F0-9]{8})\s*used by\s*(\S+@\S+)', container_text, re.IGNORECASE)
    for code, used_by in used_pattern:
        result["used"][code] = used_by

    # 如果 Copy 按钮方式没找到可用码，用备选方案
    if not result["available"]:
        for code in all_codes:
            if code not in result["used"]:
                result["available"].append(code)
    else:
        result["available"] = [c for c in result["available"] if c not in result["used"]]

    result["generated"] = len(all_codes)
    result["total"] = len(all_codes)

    return result


def logout_account(page, context):
    """登出当前账号 - 直接清 cookie 最可靠"""
    context.clear_cookies()
    page.goto(BASE_URL)
    time.sleep(1)


# ==================== 主流程 ====================

def _ask_start_registration(state, all_emails):
    """询问是否启动注册"""
    # 检查邀请码池
    if not state["invite_pool"]:
        log("邀请码池为空，无法启动注册", "ERROR")
        return

    print(f"\n{'=' * 60}")

    # 根据 state 版本计算可注册邮箱
    if state.get("version") == "2.0":
        completed_set = set(e for e, a in state["accounts"].items() if a["status"] == "completed")
        failed_set = set(e for e, a in state["accounts"].items() if a["status"] == "failed")
    else:
        completed_set = set(state.get("completed_emails", []))
        failed_set = set(state.get("failed_emails", []))

    fresh = [a for a in all_emails if a["email"] not in completed_set and a["email"] not in failed_set]
    retry = [a for a in all_emails if a["email"] in failed_set and a["email"] not in completed_set]

    log(f"可注册邮箱: {len(fresh)} 新 + {len(retry)} 重试 = {len(fresh) + len(retry)} 个")
    log(f"可用邀请码: {len(state['invite_pool'])} (池) + {len(state['output_codes'])} (输出)")

    confirm = input("\n启动批量注册? (Y/n): ").strip().lower()
    if confirm == "n":
        print("已退出")
        return

    email_file = os.path.join(_PROJECT_ROOT, "data", "outlook令牌号.csv")

    # 无头模式选择
    headless_input = input("使用无头模式(无界面)? (y/N): ").strip().lower()
    headless = headless_input == "y"

    run_batch(email_file, headless=headless)


def run_preflight(mode='smart'):
    """执行注册前校验

    mode:
        - 'smart': 智能模式，只检查邀请码不完整的账号（默认）
        - 'full': 完整模式，检查所有已注册账号
        - 'skip': 跳过模式，完全信任 state.json，不登录
        - 'force': 强制模式，忽略 state.json，登录所有邮箱验证
    """
    print("=" * 60)
    print("  EvoMap 注册前校验 & 初始化工具")
    print("=" * 60)

    # 1. 从邮箱资源池加载所有邮箱
    email_file = os.path.join(_PROJECT_ROOT, "data", "outlook令牌号.csv")
    if not os.path.exists(email_file):
        log(f"邮箱资源池文件不存在: {email_file}", "ERROR")
        return

    all_emails = load_emails(email_file)
    log(f"从邮箱资源池加载: {len(all_emails)} 个邮箱")

    # 2. 加载当前状态
    state = load_state()

    log(f"\n当前 state.json 状态:")
    log(f"  invite_pool: {len(state['invite_pool'])} 个")
    log(f"  output_codes: {len(state['output_codes'])} 个")

    if state.get("version") == "2.0":
        accounts = state.get("accounts", {})
        completed_count = sum(1 for a in accounts.values() if a["status"] == "completed")
        failed_count = sum(1 for a in accounts.values() if a["status"] == "failed")
        log(f"  accounts: {len(accounts)} 个 (completed: {completed_count}, failed: {failed_count})")
    else:
        log(f"  completed_emails: {len(state.get('completed_emails', []))} 个")
        log(f"  failed_emails: {len(state.get('failed_emails', []))} 个")

    # 3. 智能分类邮箱
    need_check = []       # 需要登录检查的
    confirmed_ok = []     # 已确认完整的
    not_registered = []   # 未注册的

    if mode == 'force':
        # 强制模式：登录所有邮箱，忽略 state.json
        log(f"\n预检模式: force（强制验证所有邮箱）")
        for email_info in all_emails:
            email = email_info["email"]
            password = email_info["password"]
            if password:
                need_check.append({"email": email, "password": password})
        log(f"  强制检查: {len(need_check)} 个邮箱（忽略 state.json）")
    else:
        # 其他模式：根据 state.json 分类
        for email_info in all_emails:
            email = email_info["email"]
            password = email_info["password"]

            if not password:
                continue

            # 检查 state.json 中的记录
            if email in state.get("accounts", {}):
                account = state["accounts"][email]
                status = account.get("status")

                # 只有 completed 状态才需要检查邀请码
                if status == "completed":
                    if mode == "skip":
                        # 跳过模式：完全信任 state.json
                        confirmed_ok.append(email)
                    elif mode == "full":
                        # 完整模式：检查所有已注册账号
                        need_check.append({"email": email, "password": password})
                    else:  # smart
                        # 智能模式：只检查邀请码不完整的
                        codes_complete = account.get("codes_generation_complete", False)
                        codes_generated = account.get("invite_codes_generated", [])

                        if codes_complete or len(codes_generated) == 3:
                            confirmed_ok.append(email)
                        else:
                            need_check.append({"email": email, "password": password})
                else:
                    # failed 或其他状态，不需要预检
                    not_registered.append(email)
            else:
                # state.json 中没有记录，认为未注册
                not_registered.append(email)

        # 4. 输出统计
        log(f"\n预检模式: {mode}")
        log(f"  邀请码完整: {len(confirmed_ok)} 个（跳过登录）")
        log(f"  需要检查: {len(need_check)} 个（登录检查邀请码）")
        log(f"  未注册: {len(not_registered)} 个（跳过登录）")

    if not need_check:
        log("\n无需预检，所有已注册账号邀请码都完整")
        # 直接跳到询问是否启动注册
        _ask_start_registration(state, all_emails)
        return

    # 5. 只登录需要检查的账号
    log(f"\n开始检查 {len(need_check)} 个账号的邀请码状态...")
    valid_accounts = need_check

    # 4. 逐个登录校验
    all_available_codes = []
    all_used_codes = {}
    confirmed_accounts = []
    login_failed_accounts = []
    unknown_accounts = []  # 密码未知的账号
    account_details = {}  # 每个账号的详细邀请码信息

    with sync_playwright() as pw:
        browser = create_browser(pw, headless=False)
        context, page = create_context(browser)

        try:
            for idx, account in enumerate(valid_accounts, 1):
                email_addr = account["email"]
                password = account["password"]
                log(f"\n[{idx}/{len(valid_accounts)}] 校验: {email_addr}")

                # 每个账号之间增加延迟，避免 429 Too Many Requests
                if idx > 1:
                    delay = random.uniform(3, 6)
                    log(f"  等待 {delay:.1f}s 避免请求过快...")
                    time.sleep(delay)

                try:
                    # 登录
                    ok = login_account(page, email_addr, password)
                    if not ok:
                        log(f"  登录失败，跳过", "WARN")
                        login_failed_accounts.append(email_addr)
                        account_details[email_addr] = {
                            "login_ok": False,
                            "codes_generated": 0,
                            "available": [],
                            "used": {},
                        }

                        # 检查是否是 429 错误
                        try:
                            body = page.locator("body").inner_text().lower()
                            if "429" in body or "too many requests" in body:
                                log(f"  检测到 429 Too Many Requests，等待 30 秒...", "WARN")
                                time.sleep(30)
                        except Exception:
                            pass

                        continue

                    log(f"  登录成功")
                    confirmed_accounts.append(email_addr)

                    # 解析邀请码
                    codes_info = parse_invite_codes(page, context)
                    log(f"  邀请码: {codes_info['generated']}/3 已生成, "
                        f"{len(codes_info['available'])} 可用, "
                        f"{len(codes_info['used'])} 已使用")

                    account_details[email_addr] = {
                        "login_ok": True,
                        "codes_generated": codes_info["generated"],
                        "available": codes_info["available"],
                        "used": codes_info["used"],
                    }

                    if codes_info["available"]:
                        log(f"  可用: {codes_info['available']}")
                        all_available_codes.extend(codes_info["available"])

                    if codes_info["used"]:
                        for code, used_by in codes_info["used"].items():
                            log(f"  已用: {code} → {used_by}")
                            all_used_codes[code] = used_by

                    # 登出
                    logout_account(page, context)

                except Exception as e:
                    log(f"  处理异常: {str(e)[:120]}", "ERROR")
                    login_failed_accounts.append(email_addr)
                    account_details[email_addr] = {
                        "login_ok": False,
                        "codes_generated": 0,
                        "available": [],
                        "used": {},
                        "error": str(e)[:200],
                    }
                    # 页面可能异常，重建 context
                    try:
                        context.close()
                    except Exception:
                        pass
                    context, page = create_context(browser)

        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    # 5. 汇总报告
    # 去重（避免重复码）
    all_available_codes = list(dict.fromkeys(all_available_codes))

    print(f"\n{'=' * 60}")
    print(f"  校验结果汇总")
    print(f"{'=' * 60}")
    log(f"确认注册成功: {len(confirmed_accounts)} 个账号")
    for e in confirmed_accounts:
        log(f"  [OK] {e}")

    if login_failed_accounts:
        log(f"登录失败: {len(login_failed_accounts)} 个账号")
        for e in login_failed_accounts:
            log(f"  [FAIL] {e}")

    if unknown_accounts:
        log(f"密码未知(跳过): {len(unknown_accounts)} 个账号")
        for a in unknown_accounts:
            log(f"  [SKIP] {a['email']}")

    log(f"\n邀请码统计:")
    log(f"  可用: {len(all_available_codes)} 个")
    log(f"  已消耗: {len(all_used_codes)} 个")
    if all_available_codes:
        log(f"  可用列表: {all_available_codes}")

    # 6. 更新 state 文件（支持 v2.0 格式）
    if state.get("version") == "2.0":
        # v2.0 格式：更新 accounts 字典
        for email in confirmed_accounts:
            if email in state["accounts"]:
                # 保留原有信息，只更新状态
                state["accounts"][email]["status"] = "completed"
            else:
                # 新账号（可能是从 accounts.txt 恢复的）
                pwd = next((a["password"] for a in valid_accounts if a["email"] == email), "password_unknown")
                state["accounts"][email] = {
                    "status": "completed",
                    "password": pwd,
                    "invite_code_used": "unknown",
                    "invite_codes_generated": account_details.get(email, {}).get("available", []),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "note": "verified by preflight"
                }

        for email in login_failed_accounts:
            if email in state["accounts"]:
                # 标记为失败
                state["accounts"][email]["status"] = "failed"
                state["accounts"][email]["error"] = "login_failed_in_preflight"
            else:
                pwd = next((a["password"] for a in valid_accounts if a["email"] == email), "password_unknown")
                state["accounts"][email] = {
                    "status": "failed",
                    "password": pwd,
                    "invite_code_used": "unknown",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "error": "login_failed_in_preflight"
                }

        # 分配邀请码：1个进 pool，其余进 output（去重）
        if all_available_codes:
            # 去除已在 pool 或 output 中的码
            existing_codes = set(state["invite_pool"] + state["output_codes"])
            new_codes = [c for c in all_available_codes if c not in existing_codes]

            if new_codes:
                state["invite_pool"] = [new_codes[0]]
                state["output_codes"] = new_codes[1:] + state["output_codes"]
            else:
                log("所有邀请码已存在于 state 中，无需更新", "WARN")

        log(f"\n更新后状态 (v2.0):")
        log(f"  invite_pool: {len(state['invite_pool'])} 个 {state['invite_pool']}")
        log(f"  output_codes: {len(state['output_codes'])} 个")
        log(f"  accounts: {len(state['accounts'])} 个")
        log(f"    - completed: {sum(1 for a in state['accounts'].values() if a['status'] == 'completed')} 个")
        log(f"    - failed: {sum(1 for a in state['accounts'].values() if a['status'] == 'failed')} 个")

    else:
        # 旧格式兼容（v1.0 或无版本）
        new_completed = list(confirmed_accounts)
        for a in unknown_accounts:
            if a["email"] in state.get("completed_emails", []):
                new_completed.append(a["email"])

        confirmed_set = set(new_completed)
        new_failed = [e for e in state.get("failed_emails", []) if e not in confirmed_set]
        for e in login_failed_accounts:
            if e not in new_failed:
                new_failed.append(e)

        for e in state.get("completed_emails", []):
            if e not in confirmed_set and e not in new_failed:
                new_failed.append(e)

        if all_available_codes:
            state["invite_pool"] = [all_available_codes[0]]
            state["output_codes"] = all_available_codes[1:]
        else:
            state["invite_pool"] = []
            state["output_codes"] = []

        state["completed_emails"] = new_completed
        state["failed_emails"] = new_failed

        log(f"\n更新后状态 (v1.0):")
        log(f"  invite_pool: {len(state['invite_pool'])} 个 {state['invite_pool']}")
        log(f"  output_codes: {len(state['output_codes'])} 个")
        log(f"  completed_emails: {len(state['completed_emails'])} 个")
        log(f"  failed_emails: {len(state['failed_emails'])} 个")

    save_state(state)
    log(f"状态文件已更新: state.json")

    # 生成 CSV 报告
    generate_csv_report(state)

    # 保存详细报告供手动查阅/修改（只存 state 中没有的信息：每账号邀请码明细）
    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "accounts": account_details,
    }
    report_file = os.path.join(_SCRIPT_DIR, "output", "preflight_report.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"详细报告已保存: {report_file}")

    # 询问是否启动注册
    _ask_start_registration(state, all_emails)


if __name__ == "__main__":
    # 支持命令行参数
    import sys
    mode = 'smart'  # 默认智能模式

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ['--full', '-f']:
            mode = 'full'
        elif arg in ['--skip', '-s']:
            mode = 'skip'
        elif arg in ['--smart', '-m']:
            mode = 'smart'
        elif arg in ['--force', '--verify']:
            mode = 'force'

    run_preflight(mode)
