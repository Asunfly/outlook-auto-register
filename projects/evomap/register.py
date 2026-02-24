"""
EvoMap 批量自动注册工具 (Playwright 浏览器自动化版)
依赖: pip install playwright requests && playwright install chromium
功能: 使用浏览器模拟点击注册 EvoMap，通过 IMAP 获取邮箱验证码，自动生成邀请码实现裂变

流程:
  1. 浏览器打开注册页 -> 输入邀请码 -> 验证
  2. 输入邮箱 -> 发送验证码 -> IMAP 获取验证码 -> 填入
  3. 设置密码 -> 勾选协议 -> 创建账号
  4. 进入账号页 -> 生成 3 个邀请码
  5. 1 个放回注册池(供下一轮使用)，2 个存入输出池
"""

import os
import re
import json
import random
import string
import time
import sys
import traceback
from datetime import datetime

# 将项目根目录的 common 目录加入 sys.path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

import requests as std_requests

from outlook_mail import OutlookMailClient
from proxy_pool import ProxyPool

# ==================== 配置 ====================

BASE_URL = "https://evomap.ai"
REGISTER_URL = f"{BASE_URL}/register"
ACCOUNT_URL = f"{BASE_URL}/account"

REGISTER_PASSWORD_LENGTH = 14
OTP_TIMEOUT = 120          # 验证码等待超时(秒) - 与ChatGPT脚本一致
OTP_POLL_INTERVAL = 3      # 验证码轮询间隔(秒) - 与ChatGPT脚本一致
STEP_DELAY = (1, 3)        # 每步操作间随机延迟(秒) - 加速批量
REGISTER_DELAY = (5, 10)   # 每个账号注册间随机延迟(秒) - 加速批量
PAGE_LOAD_TIMEOUT = 30     # 页面加载超时(秒)
ELEMENT_WAIT_TIMEOUT = 20  # 元素等待超时(秒)

# 文件路径（相对于脚本所在目录）
STATE_FILE = os.path.join(_SCRIPT_DIR, "output", "state.json")
DEFAULT_EMAIL_FILE = os.path.join(_PROJECT_ROOT, "data", "outlook令牌号.csv")
MIHOMO_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "data", "mihomo.json")
MIHOMO_CONFIG_EXAMPLE = os.path.join(_PROJECT_ROOT, "data-templates", "mihomo.example.json")



# ==================== 自定义异常 ====================

class InviteCodeInvalidError(Exception):
    """邀请码已失效（被消耗或过期）- 不应重试"""
    pass


class EmailAlreadyRegisteredError(Exception):
    """邮箱已被注册 - 不应重试"""
    pass


class RateLimitError(Exception):
    """请求被限流 - 可以稍后重试"""
    pass


class ServerError(Exception):
    """服务器错误 (502/503/500) - 可以稍后重试"""
    pass


# ==================== 工具函数 ====================

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def random_delay(low=None, high=None):
    if low is None:
        low, high = STEP_DELAY
    time.sleep(random.uniform(low, high))


def human_type(locator, text, delay_range=(50, 150)):
    """模拟人类打字速度输入 (Playwright 内置 delay 参数，单位毫秒)"""
    locator.fill("")
    locator.type(text, delay=random.randint(*delay_range))


def generate_password(length=REGISTER_PASSWORD_LENGTH):
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%&*"
    pwd = [random.choice(lower), random.choice(upper),
           random.choice(digits), random.choice(special)]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


# ==================== 状态管理 ====================

def load_state():
    """加载运行状态（邀请码池、已完成账号等）"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            # 兼容旧格式
            if "version" not in state:
                log("检测到旧格式state，请运行 migrate_state.py 迁移", "WARN")
                return {
                    "version": "1.0",
                    "invite_pool": state.get("invite_pool", []),
                    "output_codes": state.get("output_codes", []),
                    "completed_emails": state.get("completed_emails", []),
                    "failed_emails": state.get("failed_emails", []),
                }
            return state
    return {
        "version": "2.0",
        "invite_pool": [],
        "output_codes": [],
        "accounts": {},
        "invite_codes_history": {},
        "statistics": {
            "total_accounts": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "total_codes_generated": 0,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }


def save_state(state):
    """保存运行状态"""
    # 更新统计信息
    if state.get("version") == "2.0":
        accounts = state.get("accounts", {})
        state["statistics"]["total_accounts"] = len(accounts)
        state["statistics"]["completed"] = sum(1 for a in accounts.values() if a["status"] == "completed")
        state["statistics"]["failed"] = sum(1 for a in accounts.values() if a["status"] == "failed")
        state["statistics"]["skipped"] = sum(1 for a in accounts.values() if a["status"] == "skipped")
        state["statistics"]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def mark_account_completed(state, email, password, invite_code_used, invite_codes_generated):
    """标记账号注册成功"""
    if state.get("version") != "2.0":
        # 旧格式兼容
        if email not in state.get("completed_emails", []):
            state.setdefault("completed_emails", []).append(email)
        return

    state["accounts"][email] = {
        "status": "completed",
        "password": password,
        "invite_code_used": invite_code_used,
        "invite_codes_generated": invite_codes_generated,
        "codes_generation_complete": len(invite_codes_generated) == 3,  # 邀请码是否完整
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    # 更新邀请码历史
    if invite_code_used:
        state["invite_codes_history"][invite_code_used] = {
            "used_by": email,
            "used_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "consumed"
        }

    # 更新统计
    state["statistics"]["total_codes_generated"] += len(invite_codes_generated)


def mark_account_failed(state, email, password, invite_code_used, error):
    """标记账号注册失败"""
    if state.get("version") != "2.0":
        # 旧格式兼容
        if email not in state.get("failed_emails", []):
            state.setdefault("failed_emails", []).append(email)
        return

    state["accounts"][email] = {
        "status": "failed",
        "password": password,
        "invite_code_used": invite_code_used,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": str(error)
    }

    # 邀请码返回池
    if invite_code_used and invite_code_used not in state["invite_pool"]:
        state["invite_pool"].append(invite_code_used)
        state["invite_codes_history"][invite_code_used] = {
            "used_by": email,
            "used_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "returned"
        }


def is_account_processed(state, email):
    """检查账号是否已处理过（只有completed状态才算已处理，failed可以重试）"""
    if state.get("version") != "2.0":
        # 旧格式兼容
        return email in state.get("completed_emails", [])

    # v2.0: 只有 completed 状态才算已处理
    account_info = state.get("accounts", {}).get(email)
    if not account_info:
        return False
    return account_info.get("status") == "completed"


def generate_csv_report(state):
    """生成 CSV 格式的任务队列（从邮箱资源池生成）"""
    import csv

    csv_file = os.path.join(_SCRIPT_DIR, "output", "registration_report.csv")

    try:
        # 读取邮箱资源池
        email_file = DEFAULT_EMAIL_FILE
        all_emails = load_emails(email_file) if os.path.exists(email_file) else []

        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)

            # 表头
            writer.writerow([
                '邮箱', '密码', '状态', '使用的邀请码',
                '生成的邀请码(已使用)', '生成的邀请码(未使用)',
                '注册时间', '错误信息'
            ])

            # 获取已使用的邀请码
            used_codes = set(state.get('invite_codes_history', {}).keys())

            # 获取 state 中的账号信息
            accounts = state.get('accounts', {})

            # 遍历邮箱资源池中的所有邮箱
            for email_info in sorted(all_emails, key=lambda x: x['email']):
                email = email_info['email']

                # 检查是否已注册
                if email in accounts:
                    info = accounts[email]
                    status = info.get('status', 'unknown')
                    password = info.get('password', '')
                    invite_used = info.get('invite_code_used', '')
                    codes_generated = info.get('invite_codes_generated', [])
                    timestamp = info.get('timestamp', '')
                    error = info.get('error', '') if status == 'failed' else ''

                    # 分类邀请码：已使用 vs 未使用
                    used_list = []
                    unused_list = []
                    for code in codes_generated:
                        if code in used_codes:
                            used_list.append(code)
                        else:
                            unused_list.append(code)

                    # 用分号分隔多个邀请码
                    used_str = '; '.join(used_list) if used_list else ''
                    unused_str = '; '.join(unused_list) if unused_list else ''

                    writer.writerow([
                        email,
                        password,
                        '成功' if status == 'completed' else '失败',
                        invite_used,
                        used_str,
                        unused_str,
                        timestamp,
                        error
                    ])
                else:
                    # 未注册的邮箱
                    writer.writerow([
                        email,
                        email_info.get('password', ''),
                        '待注册',
                        '',
                        '',
                        '',
                        '',
                        ''
                    ])

        log(f"CSV 报告已生成: {csv_file}")

    except Exception as e:
        log(f"生成 CSV 报告失败: {e}", "ERROR")

    except Exception as e:
        log(f"生成 CSV 报告失败: {e}", "ERROR")


def handle_invalid_invite_code(state, invalid_code, browser=None):
    """
    处理邀请码失效的情况
    1. 从 output_codes 补充新码
    2. 如果 output_codes 为空，查找失效码关联的账号并尝试生成新码
    3. 返回是否成功补充新码
    """
    log(f"邀请码 {invalid_code} 已失效，开始处理...", "WARN")

    # 尝试从 output_codes 补充
    if state["output_codes"]:
        new_code = state["output_codes"].pop(0)
        # 去重检查
        if new_code not in state["invite_pool"]:
            state["invite_pool"].append(new_code)
            log(f"从 output_codes 补充新邀请码: {new_code}")
            return True
        else:
            log(f"邀请码 {new_code} 已在池中，跳过", "WARN")
            return handle_invalid_invite_code(state, invalid_code, browser)  # 递归尝试下一个

    # output_codes 为空，查找失效码关联的账号
    log("output_codes 已空，尝试从失效码关联账号生成新码...", "WARN")

    code_history = state.get("invite_codes_history", {}).get(invalid_code, {})
    used_by_email = code_history.get("used_by")

    if not used_by_email:
        log(f"无法找到邀请码 {invalid_code} 的关联账号", "ERROR")
        return False

    account_info = state.get("accounts", {}).get(used_by_email, {})
    if account_info.get("status") != "completed":
        log(f"关联账号 {used_by_email} 未成功注册，无法生成新码", "ERROR")
        return False

    # 如果提供了 browser，尝试自动登录生成邀请码
    if browser:
        log(f"尝试自动登录 {used_by_email} 生成新邀请码...")
        try:
            generated_codes = login_and_generate_codes(browser, used_by_email, account_info.get("password", ""))

            if generated_codes:
                log(f"成功生成 {len(generated_codes)} 个邀请码: {generated_codes}")

                # 去重后添加到 invite_pool
                for code in generated_codes:
                    if code not in state["invite_pool"] and code not in state["output_codes"]:
                        state["invite_pool"].append(code)
                        log(f"邀请码 {code} 已添加到 invite_pool")

                return len(state["invite_pool"]) > 0
            else:
                log(f"登录 {used_by_email} 失败或未生成邀请码", "ERROR")
                return False
        except Exception as e:
            log(f"自动生成邀请码失败: {e}", "ERROR")
            return False
    else:
        # 没有提供 browser，提示手动运行
        log(f"请手动运行: python manual_generate_codes.py {used_by_email} {account_info.get('password', '')}", "ERROR")
        return False


def login_and_generate_codes(browser, email, password):
    """
    登录已注册账号并生成邀请码
    返回生成的邀请码列表
    """
    try:
        # 创建新的浏览器上下文
        context = browser.new_context()
        page = context.new_page()

        # 调用现有的登录和生成邀请码函数
        generated_codes = relogin_and_generate_codes(context, page, email, password)

        # 关闭上下文
        context.close()

        return generated_codes
    except Exception as e:
        log(f"登录并生成邀请码失败: {e}", "ERROR")
        return []


def handle_email_registered(state, email, password, invite_code, browser):
    """
    处理邮箱已注册的情况
    1. 尝试登录该邮箱验证邀请码生成情况
    2. 如果登录成功，生成邀请码并放入池中（去重）
    3. 如果登录失败，标记为 failed
    4. 邀请码放回池中
    """
    log(f"邮箱 {email} 已被注册，尝试登录验证...", "WARN")

    # 邀请码先放回池中
    if invite_code not in state["invite_pool"]:
        state["invite_pool"].insert(0, invite_code)
        log(f"邀请码 {invite_code} 已放回池中")

    # 尝试登录并生成邀请码
    try:
        log(f"尝试登录 {email} 生成邀请码...")
        generated_codes = login_and_generate_codes(browser, email, password)

        if generated_codes:
            log(f"成功生成 {len(generated_codes)} 个邀请码: {generated_codes}")

            # 去重后添加到 invite_pool 和 output_codes
            for code in generated_codes:
                if code not in state["invite_pool"] and code not in state["output_codes"]:
                    if len(state["invite_pool"]) == 0:
                        state["invite_pool"].append(code)
                        log(f"邀请码 {code} 已添加到 invite_pool")
                    else:
                        state["output_codes"].append(code)
                        log(f"邀请码 {code} 已添加到 output_codes")

            return True
        else:
            log(f"登录 {email} 失败或未生成邀请码", "ERROR")
            return False

    except Exception as e:
        log(f"处理已注册邮箱 {email} 时出错: {e}", "ERROR")
        return False


# ==================== 邮箱文件解析 ====================

def load_mihomo_config():
    """加载 Mihomo 配置文件"""
    if not os.path.exists(MIHOMO_CONFIG_FILE):
        return None

    try:
        with open(MIHOMO_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

        if not config.get("enabled", False):
            return None

        return config
    except Exception as e:
        log(f"加载 Mihomo 配置失败: {e}", "WARN")
        return None


def load_emails(file_path):
    """
    从文件加载邮箱列表
    支持格式: email----password----client_id----refresh_token
    CSV 文件自动跳过第一行表头
    """
    if not os.path.exists(file_path):
        log(f"邮箱文件不存在: {file_path}", "ERROR")
        return []

    accounts = []
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        line = line.strip().rstrip("\t")
        if not line or line.startswith("#"):
            continue
        # 跳过 CSV 表头
        if i == 0 and ("卡号" in line or "email" in line.lower()):
            continue

        parts = line.split("----")
        if len(parts) != 4:
            log(f"格式错误，跳过第 {i+1} 行: {line[:50]}...", "WARN")
            continue

        email_addr, pwd, client_id, refresh_token = [p.strip() for p in parts]
        accounts.append({
            "email": email_addr,
            "password": pwd,
            "client_id": client_id,
            "refresh_token": refresh_token,
        })

    return accounts


# ==================== 邮箱验证码配置 ====================

# EvoMap 验证邮件发件人
EVOMAP_SENDER = "evolvemap.ai"

# Web API 地址（用于快速取码兜底）
EVOMAP_WEB_API_URL = "http://acg-mail-b.getcharzp.cn/api/v1/tool/mail/search"


# ==================== Playwright 浏览器操作 ====================

def create_browser(pw, headless=False, proxy=None):
    """启动浏览器（整个批量注册期间只启动一次）

    Args:
        pw: Playwright 实例
        headless: 是否无头模式
        proxy: 代理地址，格式如 http://192.168.100.1:7890
    """
    launch_options = {"headless": headless}

    if proxy:
        # Playwright 代理格式: {"server": "http://host:port"}
        launch_options["proxy"] = {"server": proxy}
        log(f"浏览器使用代理: {proxy}")

    browser = pw.chromium.launch(**launch_options)
    return browser


def create_context(browser):
    """创建隔离的浏览器上下文（每个账号一个，等同于无痕窗口）"""
    major = random.choice([131, 133, 136])
    context = browser.new_context(
        viewport={
            "width": random.randint(1200, 1400),
            "height": random.randint(800, 1000),
        },
        user_agent=(
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.{random.randint(6700, 7200)}"
            f".{random.randint(50, 200)} Safari/537.36"
        ),
    )
    # 反检测 JS
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    """)
    page = context.new_page()
    page.set_default_timeout(ELEMENT_WAIT_TIMEOUT * 1000)  # Playwright 用毫秒
    page.set_default_navigation_timeout(PAGE_LOAD_TIMEOUT * 1000)
    return context, page


def dismiss_onboarding(page):
    """关闭新手引导弹窗（温和方式，避免破坏页面）"""
    # 先尝试点击常见的关闭按钮
    for text in ["Skip", "Close", "Done", "Finish", "Next", "Got it", "×"]:
        try:
            btn = page.locator(f"xpath=//button[contains(text(), '{text}')]").first
            if btn.is_visible(timeout=1000):
                btn.click(timeout=2000)
                log(f"关闭新手引导 (点击 '{text}')")
                random_delay(0.5, 1)
                break
        except Exception:
            pass

    # 按 Escape 键关闭可能的弹窗
    try:
        page.keyboard.press("Escape")
        random_delay(0.3, 0.5)
    except Exception:
        pass

    log("新手引导清理完成")


def register_one_account(browser, account, invite_code):
    """
    用 Playwright 注册单个 EvoMap 账号
    返回: (success, generated_invite_codes, error_type)
    error_type: "invite_code_invalid" | "email_registered" | "rate_limit" | "server_error" | "other"
    """
    email_addr = account["email"]
    client_id = account["client_id"]
    refresh_token = account["refresh_token"]
    evomap_password = account["password"]  # 使用 outlook 邮箱密码

    log(f"{'='*50}")
    log(f"开始注册: {email_addr}")
    log(f"使用邀请码: {invite_code}")
    log(f"EvoMap 密码: {evomap_password}")
    log(f"{'='*50}")

    context, page = create_context(browser)
    try:
        # ========== 步骤1: 打开注册页，输入邀请码 ==========
        log("步骤1: 打开注册页，输入邀请码")
        page.goto(REGISTER_URL)
        random_delay(2, 4)

        # 检测页面是否正常加载（排除 502/503 等服务器错误）
        title = page.title()
        if "Bad gateway" in title or "502" in title or "503" in title:
            raise ServerError("EvoMap 服务器错误 (502/503)，稍后重试")

        invite_input = page.locator("input[placeholder='Invite Code']")
        human_type(invite_input, invite_code)
        random_delay(0.5, 1)

        verify_btn = page.locator("xpath=//button[contains(text(), 'Verify Invite Code')]")
        verify_btn.click(force=True)
        log("邀请码已提交")
        random_delay()

        # 验证邀请码是否成功：检查 Email 输入框是否出现
        email_input = page.locator("input[placeholder='Email']")
        try:
            email_input.wait_for(state="visible", timeout=15000)
        except PlaywrightTimeout:
            # 检查页面是否有错误信息
            page_text = page.locator("body").inner_text().lower()

            # 关键检测1: 邀请码已失效
            if any(keyword in page_text for keyword in ["invalid", "expired", "used", "not found"]):
                raise InviteCodeInvalidError(f"邀请码 {invite_code} 已失效（被消耗或过期）")

            # 服务器错误
            if "500" in page_text or "error" in page_text:
                raise ServerError(f"邀请码验证步骤服务器错误，跳过此邮箱")

            raise Exception(f"邀请码验证步骤异常（Email输入框未出现），跳过此邮箱")

        # 在点击发送前，先创建邮件客户端并记录已有邮件
        log("创建邮件客户端并记录邮件状态...")
        mail_client = OutlookMailClient(
            email=email_addr,
            client_id=client_id,
            refresh_token=refresh_token,
            sender_filter=EVOMAP_SENDER,
            web_api_url=EVOMAP_WEB_API_URL,
        )
        known_ids = mail_client.get_known_ids()

        human_type(email_input, email_addr)
        random_delay(0.5, 1)

        send_time = time.time()  # 记录发送时间，用于 Web API 兜底
        send_btn = page.locator("xpath=//button[contains(text(), 'Send Code')]")
        send_btn.click(force=True)
        log("验证码发送按钮已点击")
        random_delay(1, 2)

        # 主动轮询: 等待 6-digit 输入框出现 或 检测错误消息
        send_code_start = time.time()
        send_code_timeout = 45
        code_input_appeared = False
        code_input_loc = page.locator("input[placeholder='Enter 6-digit code']")
        while time.time() - send_code_start < send_code_timeout:
            if code_input_loc.count() > 0 and code_input_loc.first.is_visible():
                code_input_appeared = True
                break

            # 检测错误消息
            body_text = page.locator("body").inner_text().lower()

            # 关键检测2: 邮箱已被注册
            if any(keyword in body_text for keyword in ["already registered", "already exists", "email is already"]):
                raise EmailAlreadyRegisteredError("邮箱已被注册")

            # 限流检测
            if any(keyword in body_text for keyword in ["too many", "rate limit", "slow down"]):
                raise RateLimitError("Send Code 被限流 (too many requests)")

            time.sleep(1)

        if code_input_appeared:
            log("页面已切换到验证码输入步骤，邮件发送成功")
        else:
            log("Send Code 后未出现验证码输入框", "ERROR")
            raise Exception("Send Code 点击后页面未跳转到验证码输入步骤")

        # ========== 步骤3: 轮询获取验证码 ==========
        log("步骤3: 轮询获取验证码")

        # 第一轮: 等待 30 秒
        otp_code = mail_client.poll_for_code(known_ids, timeout=30, send_time=send_time)

        # 第一轮没收到? 尝试重发验证码
        if not otp_code:
            log("30s 未收到验证码，尝试重发...")
            resend_btn = page.locator(
                "xpath=//button[contains(text(), 'Resend') or contains(text(), 'resend') or contains(text(), '重发')]"
            )
            if resend_btn.count() > 0:
                resend_btn.first.click()
                log("重发按钮已点击")
                random_delay(1, 2)
            else:
                log("未找到重发按钮，继续等待", "WARN")

            # 重发后更新已知邮件 ID
            known_ids = mail_client.get_known_ids()

            # 第二轮: 再等待 30 秒
            send_time = time.time()
            otp_code = mail_client.poll_for_code(known_ids, timeout=30, send_time=send_time)

        if not otp_code:
            raise Exception("验证码获取超时（已尝试重发）")

        code_input = page.locator("input[placeholder='Enter 6-digit code']")
        human_type(code_input, otp_code)
        random_delay(0.5, 1)

        continue_btn = page.locator("xpath=//button[contains(text(), 'Continue with Email')]")
        continue_btn.click(force=True)
        log("验证码已提交")
        random_delay()

        # ========== 步骤4: 设置密码，勾选协议，创建账号 ==========
        log("步骤4: 设置密码，创建账号")
        pwd_input = page.locator("input[placeholder='Password']")
        human_type(pwd_input, evomap_password)
        random_delay(0.5, 1)

        # 勾选 EULA checkbox
        eula_checkbox = page.locator("input[type='checkbox']")
        try:
            eula_checkbox.click(timeout=5000)
        except PlaywrightTimeout:
            # 备选: 尝试 label 或相邻元素
            eula_alt = page.locator(
                "xpath=//*[contains(text(), 'End User License Agreement')]/preceding-sibling::*[1]"
            )
            eula_alt.click()
        random_delay(0.3, 0.8)

        create_btn = page.locator("xpath=//button[contains(text(), 'Create Account')]")
        create_btn.click(force=True)
        log("创建账号请求已提交")

        # 等待跳转（注册成功后会跳转到 /ask 页面）
        page.wait_for_function(
            "() => !window.location.href.includes('/register')",
            timeout=45000,
        )
        log(f"注册成功! 跳转到: {page.url}")

        # ========== 步骤5: 重新登录后生成邀请码 ==========
        # 关闭当前上下文（避免新手引导干扰），用新上下文登录再生成邀请码
        log("步骤5: 重新登录生成邀请码（避开新手引导）")
        context.close()
        random_delay(1, 2)

        ctx2, page2 = create_context(browser)
        try:
            generated_codes = _login_and_generate_codes(page2, ctx2, email_addr, evomap_password)
        finally:
            ctx2.close()

        log(f"注册完成: {email_addr} | 邀请码: {generated_codes}")
        return True, generated_codes, None

    except InviteCodeInvalidError as e:
        error_msg = str(e)
        log(f"注册失败 [{email_addr}]: {error_msg}", "ERROR")
        return False, [], "invite_code_invalid"

    except EmailAlreadyRegisteredError as e:
        error_msg = str(e)
        log(f"注册失败 [{email_addr}]: {error_msg}", "ERROR")
        return False, [], "email_registered"

    except RateLimitError as e:
        error_msg = str(e)
        log(f"注册失败 [{email_addr}]: {error_msg}", "ERROR")
        return False, [], "rate_limit"

    except ServerError as e:
        error_msg = str(e)
        log(f"注册失败 [{email_addr}]: {error_msg}", "ERROR")
        return False, [], "server_error"

    except Exception as e:
        error_msg = str(e)
        log(f"注册失败 [{email_addr}]: {error_msg}", "ERROR")
        traceback.print_exc()
        return False, [], "other"
    finally:
        try:
            context.close()
        except Exception:
            pass


def _login_and_generate_codes(page, context, email_addr, password):
    """重新登录并生成邀请码（在无新手引导的环境中）"""
    log(f"登录 {email_addr} ...")
    page.goto(f"{BASE_URL}/login")
    random_delay(2, 3)

    # 输入邮箱和密码
    email_input = page.locator("input[type='text'], input[placeholder*='Email']").first
    human_type(email_input, email_addr, delay_range=(30, 80))
    random_delay(0.3, 0.5)

    pwd_input = page.locator("input[type='password'], input[placeholder*='Password']").first
    human_type(pwd_input, password, delay_range=(30, 80))
    random_delay(0.3, 0.5)

    login_btn = page.locator("xpath=//button[contains(text(), 'Continue with Email')]")

    # 尝试点击 2-3 次，每次间隔 2 秒（服务不稳定时可能需要多次点击）
    for attempt in range(3):
        try:
            if login_btn.first.is_visible(timeout=5000):
                login_btn.first.click(force=True, timeout=5000)
                log(f"登录按钮已点击 (尝试 {attempt + 1}/3)")
                time.sleep(2)

                # 检查是否已经跳转
                try:
                    page.wait_for_function(
                        "() => !window.location.href.includes('/login')",
                        timeout=3000,
                    )
                    log(f"登录成功! 跳转到: {page.url}")
                    break  # 跳转成功，退出重试循环
                except PlaywrightTimeout:
                    if attempt < 2:
                        continue  # 还没跳转，继续重试点击
        except PlaywrightTimeout:
            if attempt == 0:
                raise Exception("登录按钮未找到")
            break

    # 最终等待跳转（如果前面的快速检测都没成功）
    try:
        page.wait_for_function(
            "() => !window.location.href.includes('/login')",
            timeout=PAGE_LOAD_TIMEOUT * 1000,
        )
        log(f"登录成功! 跳转到: {page.url}")
    except PlaywrightTimeout:
        raise Exception("登录超时")
    random_delay(1, 2)

    # 进入账号页
    page.goto(ACCOUNT_URL)
    random_delay(2, 4)

    # 关闭可能存在的新手引导弹窗
    dismiss_onboarding(page)

    # 生成 3 个邀请码
    generated_codes = []
    for i in range(3):
        log(f"生成邀请码 {i+1}/3...")
        gen_btn = page.locator("xpath=//button[contains(text(), 'Generate Invite Code')]")
        if gen_btn.count() == 0:
            log(f"第 {i+1} 个邀请码生成按钮未找到（可能已达上限）", "WARN")
            break
        try:
            gen_btn.first.scroll_into_view_if_needed()
            gen_btn.first.click(force=True)
            random_delay(2, 4)
        except PlaywrightTimeout:
            log(f"第 {i+1} 个邀请码生成按钮点击超时", "WARN")
            break

    # 等待邀请码显示（生成后需要时间渲染）
    random_delay(3, 5)

    # 等待至少有 Copy 按钮出现
    try:
        page.locator("xpath=//button[text()='Copy']").first.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeout:
        log("Copy 按钮未出现，可能邀请码未生成", "WARN")

    # 精确定位邀请码容器 div: border-primary col-span-2
    invite_container = page.locator("div[class*='border-primary'][class*='col-span-2']")
    if invite_container.count() == 0:
        log("未找到邀请码容器 div，退回 main 元素", "WARN")
        invite_container = page.locator("main")

    search_root = invite_container.first

    # 提取邀请码（邀请码是 8 位十六进制大写字符串，紧跟 Copy 按钮）
    copy_buttons = search_root.locator("xpath=.//button[text()='Copy']").all()
    for btn in copy_buttons:
        try:
            code_el = btn.evaluate(
                "el => { var prev = el.previousElementSibling || el.parentElement.previousElementSibling; return prev ? prev.textContent.trim() : ''; }"
            )
            if re.match(r'^[A-F0-9]{8}$', code_el):
                generated_codes.append(code_el)
        except Exception:
            pass

    # 备选方案: 用正则从容器文本提取
    if not generated_codes:
        try:
            page_text = search_root.inner_text()
            generated_codes = re.findall(r'\b([A-F0-9]{8})\b', page_text)
            generated_codes = list(dict.fromkeys(generated_codes))
        except Exception:
            pass

    # 最终备选: 通过 API 获取
    if not generated_codes:
        log("未能从页面提取邀请码，尝试通过 API 获取", "WARN")
        cookies = {c['name']: c['value'] for c in context.cookies()}
        generated_codes = _fetch_invite_codes_api(cookies)

    log(f"生成的邀请码: {generated_codes}")
    return generated_codes


def _fetch_invite_codes_api(cookies):
    """通过 API 获取邀请码（使用浏览器 session cookie）"""
    try:
        headers = {
            "Accept": "application/json",
            "Referer": f"{BASE_URL}/account",
        }
        r = std_requests.get(
            f"{BASE_URL}/api/hub/invite/my",
            headers=headers,
            cookies=cookies,
            timeout=15,
        )
        data = r.json()
        codes = []
        for item in data.get("codes", []):
            if isinstance(item, dict):
                codes.append(item.get("code", ""))
            elif isinstance(item, str):
                codes.append(item)
        return codes
    except Exception as e:
        log(f"API 获取邀请码失败: {e}", "ERROR")
        return []


# ==================== 批量注册主流程 ====================

def run_batch(email_file, proxy=None, proxy_pool=None, headless=False):
    """
    批量注册主流程:
    - 从邮箱文件加载账号
    - 使用邀请码池循环注册
    - 每注册一个账号生成3个邀请码：1个回池，2个输出

    Args:
        email_file: 邮箱文件路径
        proxy: 固定代理地址（与 proxy_pool 二选一）
        proxy_pool: ProxyPool 实例（支持节点切换）
        headless: 是否无头模式
    """
    accounts = load_emails(email_file)
    if not accounts:
        log("没有可用邮箱", "ERROR")
        return

    state = load_state()

    # 读取 preflight 报告（如果存在），打印每账号邀请码概况
    report_file = os.path.join(_SCRIPT_DIR, "output", "preflight_report.json")
    if os.path.exists(report_file):
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                report = json.load(f)
            log(f"Preflight 报告: {report.get('timestamp', '?')}")
            for email, detail in report.get("accounts", {}).items():
                if detail.get("login_ok"):
                    log(f"  {email}: {detail['codes_generated']}/3 码, "
                        f"{len(detail['available'])} 可用")
        except Exception as e:
            log(f"读取 preflight 报告失败: {e}", "WARN")

    # 筛选待注册账号
    remaining = [a for a in accounts if not is_account_processed(state, a["email"])]
    total = len(remaining)

    if not remaining:
        log("所有邮箱已注册完成")
        return

    if not state["invite_pool"]:
        log("邀请码池为空！请提供初始邀请码", "ERROR")
        return

    log(f"{'#'*60}")
    log(f"  EvoMap 批量自动注册 (Playwright)")
    log(f"  待注册: {total} | 邀请码池: {len(state['invite_pool'])} 个")
    log(f"{'#'*60}")

    success_count = 0
    fail_count = 0
    browser_restart_needed = False  # 标记是否需要重启浏览器

    # 获取当前代理（如果使用代理池）
    def get_current_proxy():
        """获取当前代理地址（处理远程 Mihomo）"""
        if proxy_pool:
            current = proxy_pool.get_proxy()
            # 远程 Mihomo 需要替换 127.0.0.1 为远程 IP
            if "127.0.0.1" in current and hasattr(proxy_pool, 'mihomo_controller'):
                remote_host = proxy_pool.mihomo_controller.control_url.split("//")[1].split(":")[0]
                current = current.replace("127.0.0.1", remote_host)
            return current
        elif proxy:
            return proxy
        return None

    current_proxy = get_current_proxy()
    if current_proxy:
        log(f"初始代理: {current_proxy}")

    with sync_playwright() as pw:
        browser = create_browser(pw, headless=headless, proxy=current_proxy)
        try:
            for idx, account in enumerate(remaining, 1):
                if not state["invite_pool"]:
                    log("邀请码池已耗尽，停止注册", "ERROR")
                    break

                invite_code = state["invite_pool"].pop(0)
                save_state(state)

                log(f"\n[{idx}/{total}] 使用邀请码 {invite_code} 注册 {account['email']}")

                success, new_codes, error_type = register_one_account(browser, account, invite_code)

                if success and new_codes:
                    success_count += 1

                    # 标记账号完成
                    mark_account_completed(
                        state,
                        account["email"],
                        account["password"],
                        invite_code,
                        new_codes
                    )

                    # 分配邀请码: 1个回池, 2个输出
                    if len(new_codes) >= 3:
                        state["invite_pool"].append(new_codes[0])
                        output_codes = new_codes[1:3]
                    elif len(new_codes) == 2:
                        state["invite_pool"].append(new_codes[0])
                        output_codes = [new_codes[1]]
                    elif len(new_codes) == 1:
                        state["invite_pool"].append(new_codes[0])
                        output_codes = []
                    else:
                        output_codes = []

                    state["output_codes"].extend(output_codes)

                    log(f"邀请码分配: 回池={new_codes[0] if new_codes else 'N/A'}, "
                        f"输出={output_codes}")
                else:
                    fail_count += 1

                    # 代理失败处理：如果使用代理池且失败，标记失败并切换节点
                    if proxy_pool and error_type in ["rate_limit", "server_error", "timeout"]:
                        old_proxy = current_proxy
                        log(f"检测到代理相关错误 ({error_type})，标记代理失败", "WARN")
                        proxy_pool.mark_failed(old_proxy)

                        # Mihomo 会自动切换节点
                        if hasattr(proxy_pool, 'mihomo_controller'):
                            new_proxy = get_current_proxy()
                            if new_proxy != old_proxy:
                                log(f"Mihomo 已切换节点: {old_proxy} → {new_proxy}", "INFO")
                                log("重启浏览器以应用新代理...", "INFO")
                                browser_restart_needed = True
                                current_proxy = new_proxy

                    # 根据错误类型决定如何处理邀请码
                    if error_type == "invite_code_invalid":
                        # 邀请码已失效，调用增强的处理函数
                        log(f"邀请码 {invite_code} 已失效，已丢弃", "WARN")

                        # 调用增强的邀请码失效处理函数（传入 browser 以支持自动生成）
                        if not handle_invalid_invite_code(state, invite_code, browser):
                            log("无法补充新邀请码，停止注册", "ERROR")
                            break  # 停止注册

                    elif error_type == "email_registered":
                        # 邮箱已注册，调用增强的处理函数
                        handle_email_registered(state, account["email"], account["password"], invite_code, browser)

                    else:
                        # 其他错误（限流、服务器错误等），邀请码放回池中
                        state["invite_pool"].insert(0, invite_code)
                        log(f"注册失败 ({error_type})，邀请码 {invite_code} 已放回池中")

                    # 标记账号失败
                    mark_account_failed(
                        state,
                        account["email"],
                        account["password"],
                        invite_code,
                        error_type or "unknown"
                    )

                save_state(state)

                # 如果需要重启浏览器（代理切换）
                if browser_restart_needed:
                    log("关闭当前浏览器...", "INFO")
                    browser.close()
                    log(f"使用新代理重启浏览器: {current_proxy}", "INFO")
                    browser = create_browser(pw, headless=headless, proxy=current_proxy)
                    browser_restart_needed = False

                if idx < total:
                    delay = random.uniform(*REGISTER_DELAY)
                    log(f"等待 {delay:.1f}s 后继续下一个...")
                    time.sleep(delay)

        finally:
            browser.close()

    log(f"\n{'#'*60}")
    log(f"  批量注册完成!")
    log(f"  总数: {total} | 成功: {success_count} | 失败: {fail_count}")
    log(f"  邀请码池剩余: {len(state['invite_pool'])} 个")
    log(f"  输出邀请码总数: {len(state['output_codes'])} 个")

    # 生成 CSV 报告
    generate_csv_report(state)

    log(f"{'#'*60}")


# ==================== 入口 ====================

def main():
    print("=" * 60)
    print("  EvoMap 批量自动注册工具 (Playwright 浏览器自动化版)")
    print("=" * 60)

    # 支持命令行参数: --auto (自动模式，不交互), --email-file <path> (指定邮箱文件)
    auto_mode = "--auto" in sys.argv

    # 解析 --email-file 参数
    custom_email_file = None
    for i, arg in enumerate(sys.argv):
        if arg == "--email-file" and i + 1 < len(sys.argv):
            custom_email_file = sys.argv[i + 1]
            break

    # 代理配置
    proxy = None
    proxy_pool = None

    # 1. 尝试加载 Mihomo 配置文件
    mihomo_config = load_mihomo_config()
    if mihomo_config:
        log(f"检测到 Mihomo 配置文件")
        log(f"  API: {mihomo_config['control_url']}")
        log(f"  代理组: {mihomo_config['proxy_group']}")

        try:
            # 判断是本地还是远程
            control_url = mihomo_config['control_url']
            strategy = mihomo_config.get('strategy', 'random')  # 默认随机

            if "127.0.0.1" in control_url or "localhost" in control_url:
                proxy_pool = ProxyPool.from_mihomo_local(
                    control_url=control_url,
                    secret=mihomo_config.get('secret', ''),
                    proxy_group=mihomo_config['proxy_group'],
                    proxy_port=mihomo_config.get('proxy_port', 7890),
                    strategy=strategy
                )
            else:
                proxy_pool = ProxyPool.from_mihomo_remote(
                    control_url=control_url,
                    secret=mihomo_config.get('secret', ''),
                    proxy_group=mihomo_config['proxy_group'],
                    proxy_port=mihomo_config.get('proxy_port', 7890),
                    strategy=strategy
                )
            log(f"Mihomo 代理池创建成功（策略: {strategy}）")
        except Exception as e:
            log(f"Mihomo 代理池创建失败: {e}", "ERROR")
            log("继续使用无代理模式", "WARN")

    # 2. 如果没有 Mihomo 配置，检查环境变量代理
    if not proxy_pool:
        env_proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                     or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"))

        if auto_mode:
            # 自动模式：使用环境变量代理
            proxy = env_proxy
            if proxy:
                log(f"使用环境变量代理: {proxy}")
            else:
                log("不使用代理")
        else:
            # 交互模式：询问是否使用代理
            if env_proxy:
                log(f"检测到环境变量代理: {env_proxy}")
                use_env = input("使用此代理? (Y/n): ").strip().lower()
                if use_env == "n":
                    proxy = input("输入代理地址 (留空=不使用): ").strip() or None
                else:
                    proxy = env_proxy
            else:
                use_proxy = input("是否使用代理? (y/N): ").strip().lower()
                if use_proxy == "y":
                    proxy = input("输入代理地址 (如 http://127.0.0.1:7890): ").strip() or None

            if proxy:
                log(f"使用固定代理: {proxy}")
            else:
                log("不使用代理")

    # 邮箱文件
    if custom_email_file:
        email_file = custom_email_file
        print(f"[Info] 使用指定邮箱文件: {email_file}")
    elif auto_mode:
        email_file = DEFAULT_EMAIL_FILE
    else:
        email_file = input(f"\n邮箱文件路径 (默认 {DEFAULT_EMAIL_FILE}): ").strip()
        if not email_file:
            email_file = DEFAULT_EMAIL_FILE

    # 初始邀请码
    state = load_state()
    if state["invite_pool"]:
        print(f"[Info] 邀请码池中已有 {len(state['invite_pool'])} 个邀请码")
        print(f"  -> {state['invite_pool']}")
    else:
        if auto_mode:
            print("[Error] 邀请码池为空，无法自动运行!")
            return
        codes_input = input("输入初始邀请码 (多个用逗号分隔): ").strip()
        if not codes_input:
            print("[Error] 必须提供至少一个邀请码!")
            return
        initial_codes = [c.strip() for c in codes_input.split(",") if c.strip()]
        state["invite_pool"].extend(initial_codes)
        save_state(state)

    # 无头模式
    if auto_mode:
        headless = False  # 自动模式默认有界面，方便观察
    else:
        headless_input = input("使用无头模式(无界面)? (y/N): ").strip().lower()
        headless = headless_input == "y"

    # 确认
    accounts = load_emails(email_file)
    # v2.0: 从 accounts 字典中提取已完成的账号
    completed = set(email for email, info in state.get("accounts", {}).items() if info.get("status") == "completed")
    remaining = [a for a in accounts if a["email"] not in completed]
    print(f"\n[Info] 邮箱总数: {len(accounts)} | 已完成: {len(completed)} | 待注册: {len(remaining)}")
    print(f"[Info] 邀请码池: {state['invite_pool']}")
    print(f"[Info] 无头模式: {'是' if headless else '否'}")

    if auto_mode:
        print("\n[Info] 自动模式，直接开始注册...")
    else:
        confirm = input("\n开始注册? (Y/n): ").strip().lower()
        if confirm == "n":
            print("已取消")
            return

    run_batch(email_file, proxy=proxy, proxy_pool=proxy_pool, headless=headless)


if __name__ == "__main__":
    main()
