"""
Outlook 邮箱收信通用模块

支持三种收信通道（按优先级）:
  1. Web API  — 第三方接口，速度最快，无需 IMAP 连接（可选，需传入 web_api_url）
  2. IMAP     — OAuth2 XOAUTH2 认证，标准方式
  3. Graph API — Microsoft Graph，适合 IMAP 被限制的场景

本模块只负责: 连接邮箱 → 搜索/获取邮件 → 返回原始内容
验证码提取等业务逻辑由调用方通过 code_extractor 回调函数决定。

用法:
    from outlook_mail import OutlookMailClient

    # 业务方定义提取逻辑
    def my_extractor(subject, body, sender):
        import re
        m = re.search(r'Verification Code: (\d{6})', subject)
        return m.group(1) if m else None

    client = OutlookMailClient(
        email="user@outlook.com",
        client_id="xxx",
        refresh_token="xxx",
        sender_filter="noreply@example.com",
        code_extractor=my_extractor,       # 业务方定义的提取函数
        web_api_url="http://xxx/api/search", # 可选，不传则不启用 Web API
    )
    known_ids = client.get_known_ids()
    # ... 触发目标网站发送验证码 ...
    code = client.poll_for_code(known_ids, timeout=60)
"""

import re
import time
import imaplib
import email as email_lib
from email.header import decode_header
from datetime import datetime

import requests as std_requests


# ==================== 默认配置 ====================

DEFAULT_POLL_TIMEOUT = 120     # 轮询超时(秒)
DEFAULT_POLL_INTERVAL = 3      # 轮询间隔(秒)
DEFAULT_FOLDERS = ["Junk", "INBOX"]


def _log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] [Mail] {msg}", flush=True)


# ==================== OAuth2 Token ====================

def get_imap_access_token(client_id, refresh_token, proxy=None):
    """
    通过 OAuth2 refresh_token 获取 IMAP access_token
    双端点回退: consumers/IMAP -> login.live.com
    返回: (access_token, imap_server)
    """
    methods = [
        {
            "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            "data": {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
            },
            "imap_server": "outlook.live.com",
            "label": "consumers/IMAP",
        },
        {
            "url": "https://login.live.com/oauth20_token.srf",
            "data": {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            "imap_server": "outlook.office365.com",
            "label": "login.live.com",
        },
    ]
    proxies = {"https": proxy, "http": proxy} if proxy else None
    last_error = ""
    for method in methods:
        try:
            r = std_requests.post(method["url"], data=method["data"], headers={
                "Content-Type": "application/x-www-form-urlencoded",
            }, timeout=30, proxies=proxies)
            resp = r.json()
            token = resp.get("access_token")
            if token:
                _log(f"IMAP token 获取成功 (via {method['label']}, server: {method['imap_server']})")
                return token, method["imap_server"]
            last_error = resp.get("error_description", resp.get("error", str(resp)))
            if "service abuse" in last_error.lower():
                raise Exception(f"账号被封禁: {last_error}")
            _log(f"IMAP token 尝试 {method['label']} 失败: {last_error[:100]}", "WARN")
        except Exception as e:
            if "封禁" in str(e):
                raise
            last_error = str(e)
            _log(f"IMAP token 尝试 {method['label']} 异常: {last_error[:100]}", "WARN")
    raise Exception(f"IMAP token 获取失败 (所有方法): {last_error[:150]}")


def get_graph_access_token(client_id, refresh_token, proxy=None):
    """通过 OAuth2 获取 Graph API access_token"""
    url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/.default",
    }
    proxies = {"https": proxy, "http": proxy} if proxy else None
    r = std_requests.post(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    }, timeout=30, proxies=proxies)
    resp = r.json()
    token = resp.get("access_token")
    if not token:
        error = resp.get("error_description", resp.get("error", str(resp)))
        if "service abuse" in error.lower():
            raise Exception(f"账号被封禁: {error}")
        raise Exception(f"Graph access token 获取失败: {error[:150]}")
    return token



# ==================== IMAP 操作 ====================

def imap_connect(email_addr, access_token, imap_server="outlook.live.com", retries=3):
    """通过 OAuth2 XOAUTH2 连接 IMAP（带 DNS 重试）"""
    last_err = None
    for attempt in range(retries):
        try:
            imap = imaplib.IMAP4_SSL(imap_server, 993)
            auth_string = f"user={email_addr}\x01auth=Bearer {access_token}\x01\x01"
            imap.authenticate("XOAUTH2", lambda x: auth_string.encode("utf-8"))
            return imap
        except OSError as e:
            last_err = e
            if "getaddrinfo" in str(e):
                time.sleep(1)
                continue
            raise
    raise last_err


def imap_search_by_sender(imap, sender_filter, folder="INBOX"):
    """在指定文件夹中搜索特定发件人的邮件，返回邮件 ID 集合"""
    imap.select(folder)
    status, msg_ids = imap.search(None, f'(FROM "{sender_filter}")')
    if status != "OK" or not msg_ids[0]:
        return set()
    return set(msg_ids[0].split())


def imap_fetch_mail(imap, mid):
    """
    获取单封邮件的原始内容
    返回: (subject, body, sender) 或 (None, None, None)
    """
    status, msg_data = imap.fetch(mid, "(RFC822)")
    if status != "OK":
        return None, None, None

    raw_email = msg_data[0][1]
    msg = email_lib.message_from_bytes(raw_email)

    sender = msg.get("From", "")
    raw_subject = msg.get("Subject", "")
    subject = ""
    if raw_subject:
        decoded = decode_header(raw_subject)
        subject = "".join(
            part.decode(enc or "utf-8") if isinstance(part, bytes) else part
            for part, enc in decoded
        )

    body = _extract_body(msg)
    return subject, body, sender


def _extract_body(msg):
    """从 email.message 对象中提取正文文本"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    charset = part.get_content_charset() or "utf-8"
                    body += part.get_payload(decode=True).decode(charset, errors="ignore")
                except Exception:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="ignore")
        except Exception:
            pass
    return body



# ==================== Graph API 操作 ====================

def graph_search_by_sender(access_token, sender_filter, top=10, proxy=None):
    """
    通过 Graph API 获取指定发件人的邮件列表
    返回: list[dict]，每个 dict 包含 id, subject, body, from, receivedDateTime
    """
    base_url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    proxies = {"https": proxy, "http": proxy} if proxy else None

    # 先尝试带 $filter
    params = {
        "$filter": f"from/emailAddress/address eq '{sender_filter}'",
        "$select": "id,subject,body,from,receivedDateTime",
        "$orderby": "receivedDateTime desc",
        "$top": str(top),
    }
    try:
        r = std_requests.get(base_url, params=params, headers=headers,
                             timeout=30, proxies=proxies)
        if r.status_code == 200:
            return r.json().get("value", [])
    except Exception:
        pass

    # filter 失败则用 $top + 后过滤
    params = {
        "$select": "id,subject,body,from,receivedDateTime",
        "$orderby": "receivedDateTime desc",
        "$top": str(top * 5),
    }
    r = std_requests.get(base_url, params=params, headers=headers,
                         timeout=30, proxies=proxies)
    if r.status_code != 200:
        _log(f"Graph 获取邮件失败: {r.status_code} {r.text[:200]}", "WARN")
        return []
    messages = r.json().get("value", [])
    return [m for m in messages
            if sender_filter.lower() in
            m.get("from", {}).get("emailAddress", {}).get("address", "").lower()]


# ==================== Web API 操作 ====================

def web_api_fetch_mails(web_api_url, email_addr, sender_filter, min_timestamp=0):
    """
    通过第三方 Web API 获取邮件列表
    搜索 JUNK 和 INBOX，返回匹配的邮件列表: [{"subject": ..., "body": ..., "sender": ...}, ...]
    带 DNS 重试: 遇到 getaddrinfo 失败时重试最多 2 次
    """
    results = []
    for mail_box in ["JUNK", "INBOX"]:
        for dns_retry in range(3):
            try:
                r = std_requests.post(
                    web_api_url,
                    json={"email": email_addr, "mail_box": mail_box, "limit": 3},
                    timeout=5,
                )
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    for item in data:
                        sender = item.get("from", "")
                        if sender_filter.lower() in sender.lower():
                            ts = item.get("time_stamp", 0) / 1000  # ms -> s
                            if ts >= min_timestamp:
                                results.append({
                                    "subject": item.get("title", ""),
                                    "body": item.get("content", ""),
                                    "sender": sender,
                                    "folder": mail_box,
                                })
                break  # 请求成功，跳出 DNS 重试
            except Exception as e:
                err_str = str(e)
                if "getaddrinfo" in err_str and dns_retry < 2:
                    time.sleep(1)
                    continue
                if dns_retry == 0:
                    _log(f"Web API ({mail_box}) 错误: {err_str[:100]}", "WARN")
                break
    return results



# ==================== 默认验证码提取器 ====================

def _default_code_extractor(subject, body, sender):
    """
    默认验证码提取器: 从邮件主题和正文中匹配 6 位数字验证码
    业务方可以替换为自定义的提取逻辑
    """
    patterns = [
        r'>\s*(\d{6})\s*<',
        r'(\d{6})\s*\n',
        r'code[:\s]+(\d{6})',
        r'verify.*?(\d{6})',
        r'(\d{6})',
    ]
    # 先检查 subject
    m = re.search(r'(\d{6})', subject)
    if m:
        return m.group(1)
    # 再检查 body
    for pattern in patterns:
        m = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


# ==================== 封装类 ====================

class OutlookMailClient:
    """
    Outlook 邮箱收信客户端

    用法:
        # 业务方定义验证码提取逻辑
        def my_extractor(subject, body, sender):
            import re
            m = re.search(r'Code: (\d{6})', subject)
            return m.group(1) if m else None

        client = OutlookMailClient(
            email="user@outlook.com",
            client_id="xxx",
            refresh_token="xxx",
            sender_filter="noreply@example.com",
            code_extractor=my_extractor,
        )
        known_ids = client.get_known_ids()
        # ... 触发网站发送验证码 ...
        code = client.poll_for_code(known_ids, timeout=60)
    """

    def __init__(self, email, client_id, refresh_token,
                 sender_filter, code_extractor=None, proxy=None,
                 folders=None, poll_interval=DEFAULT_POLL_INTERVAL,
                 web_api_url=None, use_graph=False):
        """
        参数:
            email: Outlook 邮箱地址
            client_id: OAuth2 client_id
            refresh_token: OAuth2 refresh_token
            sender_filter: 目标网站发件人（用于 IMAP FROM 搜索和 Web API 匹配）
            code_extractor: 验证码提取回调 (subject, body, sender) -> str|None
                            不传则使用默认的 6 位数字提取器
            proxy: 代理地址
            folders: 搜索的邮箱文件夹列表，默认 ["Junk", "INBOX"]
            poll_interval: 轮询间隔秒数
            web_api_url: 第三方 Web API 地址，传入则启用 Web API 通道，不传则不启用
            use_graph: 是否使用 Graph API（替代 IMAP）
        """
        self.email = email
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.sender_filter = sender_filter
        self.code_extractor = code_extractor or _default_code_extractor
        self.proxy = proxy
        self.folders = folders or list(DEFAULT_FOLDERS)
        self.poll_interval = poll_interval
        self.web_api_url = web_api_url
        self.use_graph = use_graph

        # 缓存 token，避免重复获取
        self._imap_token = None
        self._imap_server = None
        self._graph_token = None

    def _ensure_imap_token(self):
        if self._imap_token is None:
            self._imap_token, self._imap_server = get_imap_access_token(
                self.client_id, self.refresh_token, self.proxy)
        return self._imap_token, self._imap_server

    def _ensure_graph_token(self):
        if self._graph_token is None:
            self._graph_token = get_graph_access_token(
                self.client_id, self.refresh_token, self.proxy)
        return self._graph_token

    def get_known_ids(self):
        """
        获取当前已有的邮件 ID 集合（用于后续区分新邮件）
        返回: set
        """
        if self.use_graph:
            return self._get_known_ids_graph()
        return self._get_known_ids_imap()

    def _get_known_ids_imap(self):
        token, server = self._ensure_imap_token()
        known = set()
        for folder in self.folders:
            try:
                imap = imap_connect(self.email, token, server)
                try:
                    known |= imap_search_by_sender(imap, self.sender_filter, folder)
                finally:
                    try: imap.logout()
                    except: pass
            except Exception as e:
                _log(f"获取已知邮件失败 [{folder}]: {e}", "WARN")
        _log(f"已知邮件: {len(known)} 封")
        return known

    def _get_known_ids_graph(self):
        token = self._ensure_graph_token()
        try:
            messages = graph_search_by_sender(token, self.sender_filter, proxy=self.proxy)
            known = {m["id"] for m in messages}
            _log(f"已知邮件 (Graph): {len(known)} 封")
            return known
        except Exception as e:
            _log(f"Graph 获取已知邮件失败: {e}", "WARN")
            return set()

    def poll_for_code(self, known_ids=None, timeout=DEFAULT_POLL_TIMEOUT, send_time=None):
        """
        轮询获取验证码

        参数:
            known_ids: 已知邮件 ID 集合（从 get_known_ids 获取）
            timeout: 超时秒数
            send_time: 发送验证码的时间戳（用于 Web API 过滤旧邮件）
        返回: 验证码字符串 或 None
        """
        if known_ids is None:
            known_ids = set()
        if send_time is None:
            send_time = time.time()

        if self.use_graph:
            return self._poll_graph(known_ids, timeout)
        return self._poll_imap(known_ids, timeout, send_time)


    def _poll_imap(self, known_ids, timeout, send_time):
        """IMAP 轮询（Web API 优先 + IMAP 补充）"""
        start = time.time()
        check_count = 0
        imap = None

        _log(f"轮询开始 - 已知 {len(known_ids)} 封旧邮件, 超时 {timeout}s")

        while time.time() - start < timeout:
            check_count += 1
            elapsed = int(time.time() - start)

            # 优先 Web API（快速，无需 IMAP 连接）
            if self.web_api_url:
                mails = web_api_fetch_mails(
                    self.web_api_url, self.email,
                    self.sender_filter, send_time - 60)
                for mail in mails:
                    code = self.code_extractor(
                        mail["subject"], mail["body"], mail["sender"])
                    if code:
                        _log(f"验证码找到 (Web API, {mail['folder']}): {code}")
                        if imap:
                            try: imap.logout()
                            except: pass
                        return code

            # IMAP 轮询
            if imap is None:
                try:
                    token, server = self._ensure_imap_token()
                    imap = imap_connect(self.email, token, server)
                except Exception as e:
                    _log(f"IMAP 连接失败: {e}", "WARN")
                    time.sleep(self.poll_interval)
                    continue

            for folder in self.folders:
                try:
                    current_ids = imap_search_by_sender(imap, self.sender_filter, folder)
                    new_ids = current_ids - known_ids

                    if new_ids:
                        _log(f"[{folder}] 发现 {len(new_ids)} 封新邮件!")
                        for mid in sorted(new_ids, key=lambda x: int(x), reverse=True):
                            subject, body, sender = imap_fetch_mail(imap, mid)
                            if subject is None:
                                continue
                            code = self.code_extractor(subject, body, sender)
                            _log(f"  邮件 {mid}: 发件人={sender}, 主题={subject}, 验证码={code}")
                            if code:
                                _log(f"验证码找到: {code} (文件夹: {folder})")
                                try: imap.logout()
                                except: pass
                                return code
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as e:
                    _log(f"[{folder}] 连接断开: {e}", "WARN")
                    imap = None
                    break
                except Exception as e:
                    _log(f"[{folder}] 读取出错: {e}", "WARN")

            _log(f"轮询第 {check_count} 次, 无验证码 ({elapsed}s/{timeout}s)")
            time.sleep(self.poll_interval)

        if imap:
            try: imap.logout()
            except: pass
        _log(f"验证码轮询超时 ({timeout}s, 共 {check_count} 次)", "ERROR")
        return None

    def _poll_graph(self, known_ids, timeout):
        """Graph API 轮询"""
        token = self._ensure_graph_token()
        start = time.time()
        check_count = 0
        fallback_tried = False
        FALLBACK_AFTER = 15

        _log(f"Graph 轮询开始 - 已知 {len(known_ids)} 封, 超时 {timeout}s")

        while time.time() - start < timeout:
            check_count += 1
            elapsed = int(time.time() - start)
            try:
                messages = graph_search_by_sender(
                    token, self.sender_filter, proxy=self.proxy)
                all_ids = {m["id"] for m in messages}
                new_ids = all_ids - known_ids

                if new_ids:
                    for msg in [m for m in messages if m["id"] in new_ids]:
                        subject = msg.get("subject", "")
                        body = msg.get("body", {}).get("content", "")
                        sender = msg.get("from", {}).get("emailAddress", {}).get("address", "")
                        code = self.code_extractor(subject, body, sender)
                        if code:
                            _log(f"验证码: {code} (Graph 新邮件, 主题: {subject})")
                            return code

                # 回退: 超时后检查已知邮件
                if not fallback_tried and elapsed >= FALLBACK_AFTER and known_ids:
                    fallback_tried = True
                    _log(f"{FALLBACK_AFTER}s 无新邮件，回退检查已知邮件...")
                    for msg in [m for m in messages if m["id"] in known_ids][:3]:
                        subject = msg.get("subject", "")
                        body = msg.get("body", {}).get("content", "")
                        sender = msg.get("from", {}).get("emailAddress", {}).get("address", "")
                        code = self.code_extractor(subject, body, sender)
                        if code:
                            _log(f"验证码: {code} (Graph 已知邮件, 主题: {subject})")
                            return code

            except Exception as e:
                _log(f"Graph API 出错: {e}", "WARN")

            _log(f"Graph 轮询第 {check_count} 次, 无验证码 ({elapsed}s/{timeout}s)")
            time.sleep(self.poll_interval)

        _log(f"Graph 轮询超时 ({timeout}s)", "ERROR")
        return None
