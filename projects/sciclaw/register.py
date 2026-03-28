"""
SciClaw 批量自动注册工具 (Playwright 浏览器自动化版)

流程:
  1. 使用邀请码进入 Onboard
  2. 填写邮箱并发送验证码
  3. 通过 Outlook IMAP 拉取验证码并完成验证
  4. 点击 DONE 完成引导并进入 chat
  5. 打开用户菜单 -> Invite Code，提取 3 个邀请码
  6. 1 个回池，2 个输出，继续下一个邮箱
"""

import argparse
import json
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

# 将项目根目录的 common 目录加入 sys.path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from outlook_mail import OutlookMailClient


# ==================== 配置 ====================

BASE_URL = "https://sciclaw.ai"
REGISTER_URL = BASE_URL
CHAT_URL = f"{BASE_URL}/chat"
INVITE_API_URL = f"{BASE_URL}/api/v1/auth/me/invitation-codes?page=1&page_size=100"

SCICLAW_SENDER = "sciclaw.ai"
SCICLAW_WEB_API_URL = os.environ.get("SCICLAW_WEB_API_URL")

OTP_TIMEOUT = 120
STEP_DELAY = (0.8, 1.8)
REGISTER_DELAY = (3, 7)
PAGE_LOAD_TIMEOUT = 45
ELEMENT_WAIT_TIMEOUT = 20

STATE_FILE = os.path.join(_SCRIPT_DIR, "output", "state.json")
DEFAULT_EMAIL_FILE = os.path.join(_PROJECT_ROOT, "data", "outlook令牌号.csv")
INVITE_CODE_PATTERN = re.compile(r"\bSC-[A-Z0-9]{8}\b")
INVITE_INVALID_PATTERNS = [
    re.compile(r"invalid\s+(access|invite)\s+code", re.I),
    re.compile(r"(access|invite)\s+code.{0,30}(invalid|expired|used|not found)", re.I),
    re.compile(r"code.{0,20}has been used", re.I),
]


# ==================== 异常 ====================

class InviteCodeInvalidError(Exception):
    """邀请码无效或已失效"""


class EmailAlreadyRegisteredError(Exception):
    """邮箱已注册"""


class RegistrationStepError(Exception):
    """注册流程中断"""


# ==================== 日志工具 ====================

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def random_delay(low=None, high=None):
    if low is None:
        low, high = STEP_DELAY
    time.sleep(random.uniform(low, high))


def human_type(locator, text, delay_range=(30, 90)):
    locator.fill("")
    locator.type(text, delay=random.randint(*delay_range))


def dedupe_keep_order(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def refill_invite_pool_if_needed(state):
    """
    当 invite_pool 为空时，自动从 output_codes 补充 1 个邀请码。
    """
    if not state.get("invite_pool") and state.get("output_codes"):
        code = state["output_codes"].pop(0)
        state["invite_pool"].append(code)
        log(f"invite_pool 为空，已从 output_codes 补充: {code}")
        return True
    return False


def save_debug_snapshot(page, tag):
    """
    保存失败现场截图和文本，便于无头调试。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dir = os.path.join(_SCRIPT_DIR, "output", "debug")
    os.makedirs(debug_dir, exist_ok=True)

    png_path = os.path.join(debug_dir, f"{ts}_{tag}.png")
    txt_path = os.path.join(debug_dir, f"{ts}_{tag}.txt")

    try:
        page.screenshot(path=png_path, full_page=True)
    except Exception:
        png_path = None

    try:
        body_text = page.locator("body").inner_text()
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(body_text)
    except Exception:
        txt_path = None

    return png_path, txt_path


# ==================== 状态管理 ====================

def _empty_state():
    return {
        "version": "1.0",
        "invite_pool": [],
        "output_codes": [],
        "accounts": {},
        "invite_codes_history": {},
        "statistics": {
            "total_accounts": 0,
            "completed": 0,
            "failed": 0,
            "total_codes_generated": 0,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if "version" not in state:
            return _empty_state()
        return state
    return _empty_state()


def save_state(state):
    accounts = state.get("accounts", {})
    stats = state.setdefault("statistics", {})
    stats["total_accounts"] = len(accounts)
    stats["completed"] = sum(1 for a in accounts.values() if a.get("status") == "completed")
    stats["failed"] = sum(1 for a in accounts.values() if a.get("status") == "failed")
    stats["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def is_account_processed(state, email):
    account = state.get("accounts", {}).get(email, {})
    return account.get("status") in {"completed", "skipped"}


def mark_account_completed(state, account, invite_code_used, invite_codes_generated):
    email = account["email"]
    state["accounts"][email] = {
        "status": "completed",
        "password": account["password"],
        "invite_code_used": invite_code_used,
        "invite_codes_generated": invite_codes_generated,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if invite_code_used:
        state["invite_codes_history"][invite_code_used] = {
            "used_by": email,
            "used_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "consumed",
        }
    state["statistics"]["total_codes_generated"] += len(invite_codes_generated)


def mark_account_failed(state, account, invite_code_used, error_type, return_invite=True):
    email = account["email"]
    state["accounts"][email] = {
        "status": "failed",
        "password": account["password"],
        "invite_code_used": invite_code_used,
        "error": error_type or "unknown",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if return_invite and invite_code_used and invite_code_used not in state["invite_pool"]:
        state["invite_pool"].insert(0, invite_code_used)
        state["invite_codes_history"][invite_code_used] = {
            "used_by": email,
            "used_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "returned",
        }


def mark_account_skipped(state, account, reason):
    email = account["email"]
    state["accounts"][email] = {
        "status": "skipped",
        "password": account["password"],
        "reason": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ==================== 邮箱文件 ====================

def load_emails(file_path):
    """
    支持格式: email----password----client_id----refresh_token
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
        if i == 0 and ("卡号" in line or "email" in line.lower()):
            continue

        parts = [x.strip() for x in line.split("----")]
        if len(parts) != 4:
            log(f"格式错误，跳过第 {i+1} 行: {line[:50]}...", "WARN")
            continue

        email_addr, pwd, client_id, refresh_token = parts
        accounts.append(
            {
                "email": email_addr,
                "password": pwd,  # SciClaw 账号密码沿用邮箱密码
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        )
    return accounts


# ==================== 浏览器 ====================

def create_browser(pw, headless=False):
    return pw.chromium.launch(headless=headless)


def create_context(browser):
    major = random.choice([131, 133, 136])
    context = browser.new_context(
        viewport={"width": random.randint(1200, 1400), "height": random.randint(800, 1000)},
        user_agent=(
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.{random.randint(6700, 7200)}.{random.randint(50, 200)} Safari/537.36"
        ),
    )
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """
    )
    page = context.new_page()
    page.set_default_timeout(ELEMENT_WAIT_TIMEOUT * 1000)
    page.set_default_navigation_timeout(PAGE_LOAD_TIMEOUT * 1000)
    return context, page


# ==================== 注册流程 ====================

def _switch_to_onboard(page):
    try:
        page.get_by_role("tab", name=re.compile("Onboard", re.I)).click(timeout=5000)
    except Exception:
        pass


def _verify_invite_code(page, invite_code):
    _switch_to_onboard(page)

    access_input = page.locator("input[placeholder='SC-XXXXXXXX']").first
    access_input.wait_for(state="visible", timeout=15000)
    human_type(access_input, invite_code)
    random_delay(0.2, 0.5)

    verify_btn = page.get_by_role("button", name=re.compile("VERIFY ACCESS CODE", re.I))
    verify_btn.click(force=True)

    # 成功判定: ACCESS GRANTED + Email address 输入框出现
    try:
        page.get_by_text(re.compile("ACCESS GRANTED", re.I)).first.wait_for(
            state="visible", timeout=18000
        )
        page.get_by_role("textbox", name=re.compile("Email address", re.I)).first.wait_for(
            state="visible", timeout=12000
        )
    except PlaywrightTimeout:
        body_text = page.locator("body").inner_text()
        body_text_lower = body_text.lower()
        png_path, txt_path = save_debug_snapshot(page, "invite_verify_failed")
        if png_path or txt_path:
            log(f"已保存失败现场: screenshot={png_path}, text={txt_path}", "WARN")

        if any(p.search(body_text) for p in INVITE_INVALID_PATTERNS):
            raise InviteCodeInvalidError(f"邀请码失效: {invite_code}")
        if any(k in body_text_lower for k in ["cloudflare", "captcha", "verify you are human"]):
            raise RegistrationStepError("触发风控验证（Cloudflare/CAPTCHA），建议稍后重试")

        snippet = body_text.strip().replace("\n", " ")[:240]
        log(f"邀请码验证未通过，页面片段: {snippet}", "WARN")
        raise RegistrationStepError("邀请码验证未进入 ACCESS GRANTED 阶段")


def _send_and_fill_email_code(page, account):
    email_addr = account["email"]
    client_id = account["client_id"]
    refresh_token = account["refresh_token"]

    email_input = page.get_by_role("textbox", name=re.compile("Email address", re.I)).first
    human_type(email_input, email_addr)
    random_delay(0.2, 0.5)

    mail_client = OutlookMailClient(
        email=email_addr,
        client_id=client_id,
        refresh_token=refresh_token,
        sender_filter=SCICLAW_SENDER,
        folders=["Junk", "INBOX"],
        web_api_url=SCICLAW_WEB_API_URL,
    )
    if SCICLAW_WEB_API_URL:
        log(f"[Mail] 启用 Web API 通道: {SCICLAW_WEB_API_URL}")
    known_ids = mail_client.get_known_ids()

    graph_client = OutlookMailClient(
        email=email_addr,
        client_id=client_id,
        refresh_token=refresh_token,
        sender_filter=SCICLAW_SENDER,
        use_graph=True,
    )
    known_graph_ids = set()
    try:
        known_graph_ids = graph_client.get_known_ids()
        log(f"[Mail] Graph 已知邮件: {len(known_graph_ids)} 封")
    except Exception as e:
        log(f"[Mail] Graph 基线获取失败，继续 IMAP/WebAPI: {e}", "WARN")

    send_time = time.time()
    send_btn = page.get_by_role("button", name=re.compile(r"^SEND CODE$", re.I)).first
    send_resp_text = ""
    send_resp_status = None
    try:
        with page.expect_response(
            lambda r: "/api/v1/auth/register/send-code" in r.url and r.request.method == "POST",
            timeout=20000,
        ) as resp_info:
            send_btn.click(force=True)
        send_resp = resp_info.value
        send_resp_status = send_resp.status
        send_resp_text = send_resp.text() or ""
        log(f"SEND CODE 响应: status={send_resp_status} body={send_resp_text[:180]}")
    except Exception:
        send_btn.click(force=True)
        log("SEND CODE 已点击（未捕获到接口响应）", "WARN")

    body_text = page.locator("body").inner_text().lower()
    body_and_resp = f"{body_text}\n{send_resp_text}".lower()
    if send_resp_status == 409 and "already registered" in body_and_resp:
        raise EmailAlreadyRegisteredError("邮箱已注册")
    if send_resp_status and send_resp_status >= 400 and send_resp_status != 409:
        raise RegistrationStepError(f"发送验证码失败: HTTP {send_resp_status}")
    if any(k in body_text for k in ["already registered", "already exists", "already been used"]):
        raise EmailAlreadyRegisteredError("邮箱已注册")
    if any(k in body_and_resp for k in ["too many", "rate limit", "frequent", "try again later"]):
        raise RegistrationStepError("发送验证码触发限流")
    if any(k in body_and_resp for k in ["invalid email", "email not found"]):
        raise RegistrationStepError("发送验证码失败：邮箱不合法或不存在")

    # 第一轮轮询 60 秒
    otp_code = mail_client.poll_for_code(known_ids, timeout=60, send_time=send_time)

    # 第一轮没收到时尝试重发一次
    if not otp_code:
        log("60s 未收到验证码，尝试重发一次...", "WARN")
        resend_clicked = False
        wait_start = time.time()
        while time.time() - wait_start < 75:
            try:
                # 倒计时期间按钮文本为 "56s"，恢复后变为 "SEND CODE"
                cur_btn = page.locator("button", has_text=re.compile(r"(^SEND CODE$|^\d+s$)", re.I)).last
                if cur_btn.count() == 0:
                    time.sleep(1)
                    continue
                if cur_btn.is_enabled():
                    text = (cur_btn.inner_text() or "").strip().upper()
                    if text == "SEND CODE":
                        cur_btn.click(force=True)
                        resend_clicked = True
                        break
                time.sleep(1)
            except Exception:
                time.sleep(1)

        if resend_clicked:
            send_time = time.time()
            known_ids = mail_client.get_known_ids()
            try:
                last_resp = page.wait_for_response(
                    lambda r: "/api/v1/auth/register/send-code" in r.url and r.request.method == "POST",
                    timeout=5000,
                )
                log(
                    f"重发响应: status={last_resp.status} body={(last_resp.text() or '')[:180]}"
                )
            except Exception:
                pass
            otp_code = mail_client.poll_for_code(known_ids, timeout=60, send_time=send_time)
        else:
            log("重发窗口内未等到可点击的 SEND CODE 按钮", "WARN")

    # WebAPI+IMAP 失败时，尝试 Graph 兜底
    if not otp_code:
        log("IMAP/WebAPI 未获取到验证码，尝试 Graph 兜底...", "WARN")
        try:
            otp_code = graph_client.poll_for_code(known_graph_ids, timeout=45)
        except Exception as e:
            log(f"Graph 兜底失败: {e}", "WARN")

    if not otp_code:
        png_path, txt_path = save_debug_snapshot(page, "otp_timeout")
        log(f"验证码超时现场: screenshot={png_path}, text={txt_path}", "WARN")
        raise RegistrationStepError("验证码获取超时")

    code_input = page.get_by_role("textbox", name=re.compile("Verification code", re.I)).first
    human_type(code_input, otp_code)
    random_delay(0.2, 0.5)

    enter_btn = page.get_by_role("button", name=re.compile("ENTER THE LAB|ENTER LABORATORY", re.I)).first
    enter_btn.click(force=True)
    log("验证码已提交，尝试进入实验室")


def _wait_until_chat(page):
    # 先等跳转到 onboarding 或 chat
    page.wait_for_function(
        "() => location.pathname.includes('/onboarding') || location.pathname.includes('/chat')",
        timeout=60000,
    )

    # 如在 onboarding，点击 DONE
    if "/onboarding" in page.url:
        done_btn = page.get_by_role("button", name=re.compile(r"^DONE$", re.I)).first
        done_btn.wait_for(state="visible", timeout=30000)

        # 有些账号首次进入需要先回复一条引导消息，DONE 才会解锁
        if not done_btn.is_enabled():
            try:
                onboarding_input = page.get_by_role(
                    "textbox", name=re.compile("Type your response", re.I)
                ).first
                onboarding_input.wait_for(state="visible", timeout=30000)
                onboarding_input.fill("Call yourself SciClaw.")
                onboarding_input.press("Enter")
                log("onboarding 自动回复已发送，等待 DONE 解锁")
            except Exception as e:
                log(f"onboarding 自动回复失败: {e}", "WARN")

            wait_start = time.time()
            while time.time() - wait_start < 90:
                try:
                    if done_btn.is_enabled():
                        break
                except Exception:
                    pass
                time.sleep(1)

        if not done_btn.is_enabled():
            png_path, txt_path = save_debug_snapshot(page, "onboarding_done_disabled")
            raise RegistrationStepError(
                f"DONE 按钮未解锁，screenshot={png_path}, text={txt_path}"
            )

        done_btn.click(force=True)
        page.wait_for_function("() => location.pathname.includes('/chat')", timeout=60000)

    # 兜底进入 chat
    if "/chat" not in page.url:
        page.goto(CHAT_URL)
        page.wait_for_function("() => location.pathname.includes('/chat')", timeout=30000)


def _extract_codes_from_text(text):
    return dedupe_keep_order(INVITE_CODE_PATTERN.findall(text.upper()))


def _extract_codes_from_invite_panel(page):
    # 打开 user menu -> Invite Code
    user_btn = page.get_by_role("button", name=re.compile("Open user menu", re.I)).first
    user_btn.click(timeout=10000)

    invite_menu = page.get_by_role("menuitem", name=re.compile("Invite Code", re.I)).first
    invite_menu.click(timeout=10000)

    # 等待面板内容
    page.get_by_text(re.compile("INVITE CODES", re.I)).first.wait_for(
        state="visible", timeout=15000
    )
    random_delay(0.3, 0.8)

    body_text = page.locator("body").inner_text()
    codes = _extract_codes_from_text(body_text)

    # 首次不足 3 个时，点击刷新按钮再读一次
    if len(codes) < 3:
        try:
            refresh_btn = page.locator(
                "div:has-text('INVITE CODES') button"
            ).first
            refresh_btn.click(timeout=5000)
            random_delay(0.6, 1.2)
            body_text = page.locator("body").inner_text()
            codes = _extract_codes_from_text(body_text)
        except Exception:
            pass

    # 关闭面板（不阻塞主流程）
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    return codes


def register_one_account(browser, account, invite_code):
    """
    返回: (success, invite_codes, error_type)
    error_type: invite_code_invalid | otp_timeout | flow_error | other
    """
    email_addr = account["email"]
    context, page = create_context(browser)
    try:
        log("=" * 50)
        log(f"开始注册: {email_addr}")
        log(f"使用邀请码: {invite_code}")
        log("=" * 50)

        page.goto(REGISTER_URL)
        random_delay(1.2, 2.2)

        _verify_invite_code(page, invite_code)
        _send_and_fill_email_code(page, account)
        _wait_until_chat(page)
        invite_codes = _extract_codes_from_invite_panel(page)

        if not invite_codes:
            raise RegistrationStepError("未提取到邀请码")

        log(f"提取邀请码: {invite_codes}")
        return True, invite_codes[:3], None

    except InviteCodeInvalidError as e:
        log(str(e), "WARN")
        return False, [], "invite_code_invalid"
    except EmailAlreadyRegisteredError as e:
        log(str(e), "WARN")
        return False, [], "email_registered"
    except RegistrationStepError as e:
        msg = str(e)
        level = "WARN" if "超时" in msg else "ERROR"
        log(msg, level)
        if "验证码" in msg and "超时" in msg:
            return False, [], "otp_timeout"
        return False, [], "flow_error"
    except Exception as e:
        png_path, txt_path = save_debug_snapshot(page, "register_unhandled_error")
        log(f"异常现场: screenshot={png_path}, text={txt_path}", "WARN")
        log(f"注册异常: {e}", "ERROR")
        traceback.print_exc()
        return False, [], "other"
    finally:
        try:
            context.close()
        except Exception:
            pass


# ==================== 批量注册 ====================

def run_batch(email_file, headless=False, limit=None):
    accounts = load_emails(email_file)
    if not accounts:
        log("没有可用邮箱账号", "ERROR")
        return

    state = load_state()
    remaining = [a for a in accounts if not is_account_processed(state, a["email"])]
    if limit and limit > 0:
        remaining = remaining[:limit]

    if not remaining:
        log("所有邮箱已注册完成")
        return
    refill_invite_pool_if_needed(state)
    if not state["invite_pool"]:
        log("邀请码池为空，请先提供初始邀请码", "ERROR")
        return

    total = len(remaining)
    success_count = 0
    fail_count = 0

    log("#" * 60)
    log(f"  SciClaw 批量自动注册")
    log(f"  待注册: {total} | 邀请码池: {len(state['invite_pool'])}")
    log("#" * 60)

    with sync_playwright() as pw:
        browser = create_browser(pw, headless=headless)
        try:
            for idx, account in enumerate(remaining, 1):
                refill_invite_pool_if_needed(state)
                if not state["invite_pool"]:
                    log("邀请码池已耗尽，停止注册", "ERROR")
                    break

                invite_code = state["invite_pool"].pop(0)
                save_state(state)

                log(f"\n[{idx}/{total}] 使用邀请码 {invite_code} 注册 {account['email']}")
                success, new_codes, error_type = register_one_account(browser, account, invite_code)

                if success:
                    success_count += 1
                    mark_account_completed(state, account, invite_code, new_codes)

                    # 分配策略: 1 个回池，剩余输出（最多 2 个）
                    if new_codes:
                        state["invite_pool"].append(new_codes[0])
                        output_codes = new_codes[1:3]
                    else:
                        output_codes = []
                    state["output_codes"].extend(output_codes)

                    log(
                        f"邀请码分配: 回池={new_codes[0] if new_codes else 'N/A'}, 输出={output_codes}"
                    )
                else:
                    fail_count += 1
                    if error_type == "email_registered":
                        mark_account_skipped(state, account, "email_already_registered")
                        if invite_code not in state["invite_pool"]:
                            state["invite_pool"].insert(0, invite_code)
                        log(f"邮箱已注册，已跳过并回池邀请码: {invite_code}", "WARN")
                        save_state(state)
                        continue

                    # 按当前项目规则：注册不成功则邀请码不消耗，统一回池
                    return_invite = True
                    mark_account_failed(
                        state,
                        account,
                        invite_code,
                        error_type or "unknown",
                        return_invite=return_invite,
                    )
                    log(f"失败({error_type})，邀请码已回池: {invite_code}", "WARN")

                save_state(state)

                if idx < total:
                    delay = random.uniform(*REGISTER_DELAY)
                    log(f"等待 {delay:.1f}s 后继续...")
                    time.sleep(delay)
        finally:
            browser.close()

    log("\n" + "#" * 60)
    log("批量注册完成")
    log(f"总数: {total} | 成功: {success_count} | 失败: {fail_count}")
    log(f"邀请码池剩余: {len(state['invite_pool'])}")
    log(f"输出邀请码总数: {len(state['output_codes'])}")
    log("#" * 60)


# ==================== 入口 ====================

def parse_args():
    parser = argparse.ArgumentParser(description="SciClaw 批量自动注册工具")
    parser.add_argument("--auto", action="store_true", help="自动模式（无交互）")
    parser.add_argument("--email-file", default=DEFAULT_EMAIL_FILE, help="邮箱文件路径")
    parser.add_argument("--initial-invite", help="初始邀请码，例如 SC-B7NDJO7I")
    parser.add_argument("--headless", action="store_true", help="使用无头浏览器")
    parser.add_argument("--limit", type=int, help="仅处理前 N 个待注册账号（调试用）")
    parser.add_argument("--proxy-mode", help="兼容 start.py 参数，当前版本忽略")
    return parser.parse_args()


def ensure_initial_invite(state, args):
    if state["invite_pool"]:
        log(f"当前邀请码池: {state['invite_pool']}")
        return state

    if refill_invite_pool_if_needed(state):
        save_state(state)
        log(f"当前邀请码池: {state['invite_pool']}")
        return state

    invite_code = (args.initial_invite or "").strip().upper()
    if not invite_code and not args.auto:
        invite_code = input("输入初始邀请码 (如 SC-B7NDJO7I): ").strip().upper()

    if not invite_code:
        raise SystemExit("邀请码池为空，且未提供 --initial-invite")

    if not INVITE_CODE_PATTERN.fullmatch(invite_code):
        log(f"邀请码格式看起来不标准: {invite_code}（仍继续）", "WARN")

    state["invite_pool"].append(invite_code)
    save_state(state)
    log(f"已写入初始邀请码: {invite_code}")
    return state


def main():
    print("=" * 60)
    print("  SciClaw 批量自动注册工具")
    print("=" * 60)

    args = parse_args()
    state = load_state()
    ensure_initial_invite(state, args)

    headless = args.headless
    if not args.auto and not args.headless:
        use_headless = input("使用无头模式(无界面)? (y/N): ").strip().lower()
        headless = use_headless == "y"

    if not os.path.exists(args.email_file):
        raise SystemExit(f"邮箱文件不存在: {args.email_file}")

    run_batch(args.email_file, headless=headless, limit=args.limit)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断，已退出")
        sys.exit(0)
    except Exception as e:
        print(f"\n[错误] {e}")
        traceback.print_exc()
        sys.exit(1)
