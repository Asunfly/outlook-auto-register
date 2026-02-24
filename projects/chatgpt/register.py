"""
ChatGPT 批量自动注册工具 (并发版)
依赖: pip install curl_cffi requests
功能: 从 CSV 文件读取 Outlook 邮箱信息，并发自动注册 ChatGPT 账号，自动获取 OTP 验证码
"""

import os
import re
import uuid
import json
import random
import string
import time
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# 将父目录加入 sys.path，以便 import outlook_mail
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

from curl_cffi import requests as curl_requests
from outlook_mail import OutlookMailClient

# 全局线程锁
_print_lock = threading.Lock()
_file_lock = threading.Lock()


# Chrome 指纹配置: impersonate 与 sec-ch-ua 必须匹配真实浏览器
_CHROME_PROFILES = [
    {
        "major": 131, "impersonate": "chrome131",
        "build": 6778, "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133, "impersonate": "chrome133a",
        "build": 6943, "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136, "impersonate": "chrome136",
        "build": 7103, "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
    {
        "major": 142, "impersonate": "chrome142",
        "build": 7540, "patch_range": (30, 150),
        "sec_ch_ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    },
]


def _random_chrome_version():
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


def _random_delay(low=0.3, high=1.0):
    time.sleep(random.uniform(low, high))


def _make_trace_headers():
    trace_id = random.randint(10**17, 10**18 - 1)
    parent_id = random.randint(10**17, 10**18 - 1)
    tp = f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01"
    return {
        "traceparent": tp, "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum", "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace_id), "x-datadog-parent-id": str(parent_id),
    }


def _generate_password(length=14):
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


def _random_name():
    first = random.choice([
        "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
        "Lucas", "Mia", "Mason", "Isabella", "Logan", "Charlotte", "Alexander",
        "Amelia", "Benjamin", "Harper", "William", "Evelyn", "Henry", "Abigail",
        "Sebastian", "Emily", "Jack", "Elizabeth",
    ])
    last = random.choice([
        "Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor",
        "Clark", "Hall", "Young", "Anderson", "Thomas", "Jackson", "White",
        "Harris", "Martin", "Thompson", "Garcia", "Robinson", "Lewis",
        "Walker", "Allen", "King", "Wright", "Scott", "Green",
    ])
    return f"{first} {last}"


def _random_birthdate():
    y = random.randint(1985, 2002)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"


# ==================== 路径配置 ====================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

DEFAULT_EMAIL_FILE = os.path.join(_PROJECT_ROOT, "data", "outlook令牌号.csv")
DEFAULT_OUTPUT_FILE = os.path.join(_SCRIPT_DIR, "output", "registered_accounts.txt")

# ChatGPT OTP 发件人
CHATGPT_SENDER = "openai.com"


def _chatgpt_code_extractor(subject, body, sender):
    """ChatGPT OTP: 从 OpenAI 验证邮件提取 6 位数字验证码"""
    # 先检查 subject (例: "Your ChatGPT code is 252788")
    m = re.search(r'(\d{6})', subject)
    if m:
        return m.group(1)
    # 再检查 body
    for pattern in [r'>\s*(\d{6})\s*<', r'(\d{6})\s*\n',
                    r'code[:\s]+(\d{6})', r'verify.*?(\d{6})', r'(\d{6})']:
        m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


class ChatGPTRegister:
    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy: str = None, tag: str = "", mail_mode: str = "imap"):
        self.tag = tag  # 线程标识，用于日志
        self.mail_mode = mail_mode  # "imap" 或 "graph"
        self.device_id = str(uuid.uuid4())
        self.auth_session_logging_id = str(uuid.uuid4())
        self.impersonate, self.chrome_major, self.chrome_full, self.ua, self.sec_ch_ua = _random_chrome_version()

        self.session = curl_requests.Session(impersonate=self.impersonate)

        self.proxy = proxy
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": random.choice([
                "en-US,en;q=0.9", "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9", "en-US,en;q=0.8",
            ]),
            "sec-ch-ua": self.sec_ch_ua, "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"', "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-full-version": f'"{self.chrome_full}"',
            "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
        })

        self.session.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        self._callback_url = None

    def _log(self, step, method, url, status, body=None):
        prefix = f"[{self.tag}] " if self.tag else ""
        lines = [
            f"\n{'='*60}",
            f"{prefix}[Step] {step}",
            f"{prefix}[{method}] {url}",
            f"{prefix}[Status] {status}",
        ]
        if body:
            try:
                lines.append(f"{prefix}[Response] {json.dumps(body, indent=2, ensure_ascii=False)[:1000]}")
            except Exception:
                lines.append(f"{prefix}[Response] {str(body)[:1000]}")
        lines.append(f"{'='*60}")
        with _print_lock:
            print("\n".join(lines))

    def _print(self, msg):
        prefix = f"[{self.tag}] " if self.tag else ""
        with _print_lock:
            print(f"{prefix}{msg}")

    # ==================== Outlook 邮件（委托 OutlookMailClient）====================

    def _create_mail_client(self, email_addr, client_id, refresh_token):
        """创建邮件客户端实例"""
        return OutlookMailClient(
            email=email_addr,
            client_id=client_id,
            refresh_token=refresh_token,
            sender_filter=CHATGPT_SENDER,
            code_extractor=_chatgpt_code_extractor,
            proxy=self.proxy,
            folders=["INBOX"],  # ChatGPT OTP 通常在 INBOX
            use_graph=(self.mail_mode == "graph"),
        )

    def get_known_mail_ids(self, email_addr, client_id, refresh_token):
        """获取已知邮件 ID（用于后续区分新邮件）"""
        try:
            client = self._create_mail_client(email_addr, client_id, refresh_token)
            known_ids = client.get_known_ids()
            self._print(f"[OTP] 已有 {len(known_ids)} 封 OpenAI 邮件")
            return known_ids, client
        except Exception as e:
            self._print(f"[OTP] 获取已有邮件 ID 失败: {e}")
            return set(), None

    def fetch_otp_from_outlook(self, mail_client, known_ids=None, timeout=120):
        """轮询获取 OTP 验证码"""
        if mail_client is None:
            self._print("[OTP] 邮件客户端未初始化")
            return None
        return mail_client.poll_for_code(known_ids=known_ids, timeout=timeout)

    # ==================== 注册流程 ====================

    def visit_homepage(self):
        url = f"{self.BASE}/"
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        self._log("0. Visit homepage", "GET", url, r.status_code,
                   {"cookies_count": len(self.session.cookies)})

    def get_csrf(self) -> str:
        url = f"{self.BASE}/api/auth/csrf"
        r = self.session.get(url, headers={"Accept": "application/json", "Referer": f"{self.BASE}/"})
        data = r.json()
        token = data.get("csrfToken", "")
        self._log("1. Get CSRF", "GET", url, r.status_code, data)
        if not token:
            raise Exception("Failed to get CSRF token")
        return token

    def signin(self, email: str, csrf: str) -> str:
        url = f"{self.BASE}/api/auth/signin/openai"
        params = {
            "prompt": "login", "ext-oai-did": self.device_id,
            "auth_session_logging_id": self.auth_session_logging_id,
            "screen_hint": "login_or_signup", "login_hint": email,
        }
        form_data = {"callbackUrl": f"{self.BASE}/", "csrfToken": csrf, "json": "true"}
        r = self.session.post(url, params=params, data=form_data, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json", "Referer": f"{self.BASE}/", "Origin": self.BASE,
        })
        data = r.json()
        authorize_url = data.get("url", "")
        self._log("2. Signin", "POST", url, r.status_code, data)
        if not authorize_url:
            raise Exception("Failed to get authorize URL")
        return authorize_url

    def authorize(self, url: str) -> str:
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.BASE}/", "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        final_url = str(r.url)
        self._log("3. Authorize", "GET", url, r.status_code, {"final_url": final_url})
        return final_url

    def register(self, email: str, password: str):
        url = f"{self.AUTH}/api/accounts/user/register"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                    "Referer": f"{self.AUTH}/create-account/password", "Origin": self.AUTH}
        headers.update(_make_trace_headers())
        r = self.session.post(url, json={"username": email, "password": password}, headers=headers)
        try: data = r.json()
        except Exception: data = {"text": r.text[:500]}
        self._log("4. Register", "POST", url, r.status_code, data)
        return r.status_code, data

    def send_otp(self):
        url = f"{self.AUTH}/api/accounts/email-otp/send"
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.AUTH}/create-account/password", "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        try: data = r.json()
        except Exception: data = {"final_url": str(r.url), "status": r.status_code}
        self._log("5. Send OTP", "GET", url, r.status_code, data)
        return r.status_code, data

    def validate_otp(self, code: str):
        url = f"{self.AUTH}/api/accounts/email-otp/validate"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                    "Referer": f"{self.AUTH}/email-verification", "Origin": self.AUTH}
        headers.update(_make_trace_headers())
        r = self.session.post(url, json={"code": code}, headers=headers)
        try: data = r.json()
        except Exception: data = {"text": r.text[:500]}
        self._log("6. Validate OTP", "POST", url, r.status_code, data)
        return r.status_code, data

    def create_account(self, name: str, birthdate: str):
        url = f"{self.AUTH}/api/accounts/create_account"
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                    "Referer": f"{self.AUTH}/about-you", "Origin": self.AUTH}
        headers.update(_make_trace_headers())
        r = self.session.post(url, json={"name": name, "birthdate": birthdate}, headers=headers)
        try: data = r.json()
        except Exception: data = {"text": r.text[:500]}
        self._log("7. Create Account", "POST", url, r.status_code, data)
        if isinstance(data, dict):
            cb = data.get("continue_url") or data.get("url") or data.get("redirect_url")
            if cb:
                self._callback_url = cb
        return r.status_code, data

    def callback(self, url: str = None):
        if not url:
            url = self._callback_url
        if not url:
            self._print("[!] No callback URL, skipping.")
            return None, None
        r = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)
        self._log("8. Callback", "GET", url, r.status_code, {"final_url": str(r.url)})
        return r.status_code, {"final_url": str(r.url)}

    # ==================== 自动注册主流程 ====================

    def run_register(self, email, password, name, birthdate, client_id, refresh_token):
        self.visit_homepage()
        _random_delay(0.3, 0.8)
        csrf = self.get_csrf()
        _random_delay(0.2, 0.5)
        auth_url = self.signin(email, csrf)
        _random_delay(0.3, 0.8)

        # 在 authorize 之前记录已有邮件，因为 authorize 可能触发 OTP 发送
        known_mail_ids, mail_client = self.get_known_mail_ids(email, client_id, refresh_token)

        final_url = self.authorize(auth_url)
        final_path = urlparse(final_url).path
        _random_delay(0.3, 0.8)

        self._print(f"Authorize → {final_path}")

        need_otp = False

        if "create-account/password" in final_path:
            self._print("全新注册流程")
            _random_delay(0.5, 1.0)
            status, data = self.register(email, password)
            if status != 200:
                raise Exception(f"Register 失败 ({status}): {data}")
            _random_delay(0.3, 0.8)
            self.send_otp()
            need_otp = True
        elif "email-verification" in final_path or "email-otp" in final_path:
            self._print("跳到 OTP 验证阶段 (authorize 已触发 OTP，不再重复发送)")
            need_otp = True
        elif "about-you" in final_path:
            self._print("跳到填写信息阶段")
            _random_delay(0.5, 1.0)
            self.create_account(name, birthdate)
            _random_delay(0.3, 0.5)
            self.callback()
            return True
        elif "callback" in final_path or "chatgpt.com" in final_url:
            self._print("账号已完成注册")
            return True
        else:
            self._print(f"未知跳转: {final_url}")
            self.register(email, password)
            self.send_otp()
            need_otp = True

        if need_otp:
            otp_code = self.fetch_otp_from_outlook(
                mail_client, known_ids=known_mail_ids)
            if not otp_code:
                raise Exception("未能获取验证码")

            _random_delay(0.3, 0.8)
            status, data = self.validate_otp(otp_code)
            if status != 200:
                self._print("验证码失败，重试...")
                known_mail_ids2, mail_client2 = self.get_known_mail_ids(email, client_id, refresh_token)
                self.send_otp()
                _random_delay(1.0, 2.0)
                otp_code = self.fetch_otp_from_outlook(
                    mail_client2, known_ids=known_mail_ids2, timeout=60)
                if not otp_code:
                    raise Exception("重试后仍未获取验证码")
                _random_delay(0.3, 0.8)
                status, data = self.validate_otp(otp_code)
                if status != 200:
                    raise Exception(f"验证码失败 ({status}): {data}")

        _random_delay(0.5, 1.5)
        status, data = self.create_account(name, birthdate)
        if status != 200:
            raise Exception(f"Create account 失败 ({status}): {data}")
        _random_delay(0.2, 0.5)
        self.callback()
        return True


# ==================== 并发批量注册 ====================

def _register_one(idx, total, email, outlook_pwd, client_id, refresh_token,
                   proxy, output_file, mail_mode="imap"):
    """单个邮箱注册任务 (在线程中运行)"""
    tag = email.split("@")[0]  # 用邮箱前缀做日志标识

    chatgpt_password = _generate_password()
    name = _random_name()
    birthdate = _random_birthdate()

    with _print_lock:
        print(f"\n{'='*60}")
        print(f"  [{idx}/{total}] 注册: {email}")
        print(f"  密码: {chatgpt_password} | 姓名: {name} | 生日: {birthdate}")
        print(f"{'='*60}")

    try:
        reg = ChatGPTRegister(proxy=proxy, tag=tag, mail_mode=mail_mode)
        reg.run_register(email, chatgpt_password, name, birthdate, client_id, refresh_token)

        # 线程安全写入结果
        with _file_lock:
            with open(output_file, "a", encoding="utf-8") as out:
                out.write(f"{email}----{chatgpt_password}\n")

        with _print_lock:
            print(f"\n[OK] [{tag}] {email} 注册成功!")
        return True, email, None

    except Exception as e:
        with _print_lock:
            print(f"\n[FAIL] [{tag}] {email} 注册失败: {e}")
            traceback.print_exc()
        return False, email, str(e)


def run_batch(input_file, output_file=None,
              max_workers=3, proxy=None, mail_mode="imap"):
    """并发批量注册"""
    if output_file is None:
        output_file = DEFAULT_OUTPUT_FILE

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    if not os.path.exists(input_file):
        print(f"[Error] 文件不存在: {input_file}")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        lines = [line.strip().rstrip("\t") for line in f if line.strip() and not line.startswith("#")]

    if not lines:
        print("[Error] 输入文件为空")
        return

    # 解析并验证格式 (支持 CSV 表头跳过)
    tasks = []
    for i, line in enumerate(lines):
        # 跳过 CSV 表头
        if i == 0 and ("卡号" in line or "email" in line.lower()):
            continue
        parts = line.split("----")
        if len(parts) != 4:
            print(f"[Warn] 格式错误，跳过: {line[:50]}...")
            continue
        tasks.append([p.strip() for p in parts])

    total = len(tasks)
    if not total:
        print("[Error] 无有效邮箱")
        return

    actual_workers = min(max_workers, total)
    mode_label = "Graph API" if mail_mode == "graph" else "IMAP"
    print(f"\n{'#'*60}")
    print(f"  ChatGPT 并发自动注册")
    print(f"  邮箱数: {total} | 并发数: {actual_workers} | 邮件方式: {mode_label}")
    print(f"  输出文件: {output_file}")
    print(f"{'#'*60}\n")

    success_count = 0
    fail_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {}
        for idx, (email, outlook_pwd, client_id, refresh_token) in enumerate(tasks, 1):
            future = executor.submit(
                _register_one, idx, total, email, outlook_pwd,
                client_id, refresh_token, proxy, output_file, mail_mode,
            )
            futures[future] = email

        for future in as_completed(futures):
            email = futures[future]
            try:
                ok, _, err = future.result()
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
                with _print_lock:
                    print(f"[FAIL] {email} 线程异常: {e}")

    elapsed = time.time() - start_time
    avg = elapsed / total if total else 0
    print(f"\n{'#'*60}")
    print(f"  注册完成! 耗时 {elapsed:.1f} 秒")
    print(f"  总数: {total} | 成功: {success_count} | 失败: {fail_count}")
    print(f"  平均速度: {avg:.1f} 秒/个")
    if success_count > 0:
        print(f"  结果文件: {output_file}")
    print(f"{'#'*60}")


def main():
    print("=" * 60)
    print("  ChatGPT 批量自动注册工具 (并发版)")
    print("=" * 60)

    # 交互式代理配置
    env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
             or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
    if env_proxy:
        print(f"[Info] 检测到环境变量代理: {env_proxy}")
        use_env = input("使用此代理? (Y/n): ").strip().lower()
        if use_env == "n":
            proxy = input("输入代理地址 (留空=不使用代理): ").strip() or None
        else:
            proxy = env_proxy
    else:
        proxy = input("输入代理地址 (如 http://127.0.0.1:7890，留空=不使用代理): ").strip() or None
    if proxy:
        print(f"[Info] 代理: {proxy}")
    else:
        print("[Info] 不使用代理")

    # 邮件获取方式选择
    print("\n邮件获取方式:")
    print("  1. IMAP (默认，适用于 IMAP scope 的 refresh_token)")
    print("  2. Graph API (适用于 Graph scope 的 refresh_token)")
    mail_choice = input("请选择 (1/2，默认 1): ").strip()
    mail_mode = "graph" if mail_choice == "2" else "imap"
    mode_label = "Graph API" if mail_mode == "graph" else "IMAP"
    print(f"[Info] 邮件获取方式: {mode_label}")

    input_file = input(f"\n邮箱文件路径 (默认 {DEFAULT_EMAIL_FILE}): ").strip()
    if not input_file:
        input_file = DEFAULT_EMAIL_FILE

    workers_input = input("并发数 (默认 3): ").strip()
    max_workers = int(workers_input) if workers_input.isdigit() and int(workers_input) > 0 else 3

    run_batch(input_file, max_workers=max_workers, proxy=proxy, mail_mode=mail_mode)


if __name__ == "__main__":
    main()
