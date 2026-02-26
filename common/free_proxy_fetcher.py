"""
免费代理自动抓取模块

从 free-proxy-list.net 抓取免费 HTTPS 代理，验证可用性后输出到代理池。
参考: https://github.com/CodeBotanist/FreeProxyProxy

用法:
    from free_proxy_fetcher import FreeProxyFetcher

    fetcher = FreeProxyFetcher()
    proxies = fetcher.fetch_and_validate()  # 抓取并验证
    fetcher.save_to_file("data/proxies.txt")  # 保存到文件

    # 一键抓取并保存
    proxies = fetch_and_save("data/proxies.txt", min_count=5)
"""

import re
import time
import html
import threading
from datetime import datetime
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ==================== 默认配置 ====================

PROXY_SOURCE_URL = "https://free-proxy-list.net/"
DEFAULT_VALIDATE_URL = "https://www.google.com"
DEFAULT_VALIDATE_TIMEOUT = 10
DEFAULT_FETCH_TIMEOUT = 15
DEFAULT_MIN_PROXIES = 3
DEFAULT_MAX_WORKERS = 20


def _log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] [FreeProxy] {msg}", flush=True)


# ==================== HTML 解析 ====================

def _strip_tags(text: str) -> str:
    """移除 HTML 标签并解码实体"""
    cleaned = re.sub(r'<[^>]*>', '', text)
    return html.unescape(cleaned).strip()


def _parse_proxy_table(html_content: str) -> List[dict]:
    """
    从 free-proxy-list.net 的 HTML 中解析代理列表

    表格列: IP | Port | Code | Country | Anonymity | Google | Https | Last Checked
    过滤条件: Google=yes AND Https=yes
    """
    tbody_match = re.search(r'<tbody[^>]*>([\s\S]*?)</tbody>', html_content, re.IGNORECASE)
    if not tbody_match:
        raise ValueError("无法在页面中找到代理表格")

    tbody_html = tbody_match.group(1)
    row_pattern = re.compile(r'<tr[^>]*>([\s\S]*?)</tr>', re.IGNORECASE)
    cell_pattern = re.compile(r'<td[^>]*>([\s\S]*?)</td>', re.IGNORECASE)

    proxies = []
    seen = set()

    for row_match in row_pattern.finditer(tbody_html):
        cells = [_strip_tags(c.group(1)) for c in cell_pattern.finditer(row_match.group(1))]

        if len(cells) < 7:
            continue

        host = cells[0]
        try:
            port = int(cells[1])
        except (ValueError, IndexError):
            continue

        google_support = cells[5].lower()
        https_support = cells[6].lower()

        if not host or port <= 0 or port > 65535:
            continue

        if google_support != "yes" or https_support != "yes":
            continue

        key = f"{host}:{port}"
        if key in seen:
            continue

        seen.add(key)
        proxies.append({
            "host": host,
            "port": port,
            "country": cells[3] if len(cells) > 3 else "",
            "anonymity": cells[4] if len(cells) > 4 else "",
        })

    return proxies


# ==================== 抓取器 ====================

class FreeProxyFetcher:
    """
    免费代理抓取器

    从 free-proxy-list.net 抓取代理，验证后提供可用代理列表。
    """

    def __init__(
        self,
        source_url: str = PROXY_SOURCE_URL,
        validate_url: str = DEFAULT_VALIDATE_URL,
        validate_timeout: int = DEFAULT_VALIDATE_TIMEOUT,
        fetch_timeout: int = DEFAULT_FETCH_TIMEOUT,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ):
        self.source_url = source_url
        self.validate_url = validate_url
        self.validate_timeout = validate_timeout
        self.fetch_timeout = fetch_timeout
        self.max_workers = max_workers

        self._raw_proxies: List[dict] = []
        self._valid_proxies: List[str] = []
        self._lock = threading.Lock()

    @property
    def valid_proxies(self) -> List[str]:
        return list(self._valid_proxies)

    def fetch(self) -> List[dict]:
        """从网站抓取代理列表（不验证）"""
        _log(f"正在从 {self.source_url} 抓取代理列表...")

        try:
            resp = requests.get(
                self.source_url,
                timeout=self.fetch_timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/131.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
        except Exception as e:
            _log(f"抓取失败: {e}", "ERROR")
            return []

        try:
            self._raw_proxies = _parse_proxy_table(resp.text)
            _log(f"解析到 {len(self._raw_proxies)} 个候选代理 (Google+HTTPS)")
            return self._raw_proxies
        except ValueError as e:
            _log(f"解析失败: {e}", "ERROR")
            return []

    def validate(self, proxies: Optional[List[dict]] = None) -> List[str]:
        """并发验证代理可用性，返回可用代理列表"""
        if proxies is None:
            proxies = self._raw_proxies

        if not proxies:
            _log("没有代理需要验证", "WARN")
            return []

        _log(f"开始验证 {len(proxies)} 个代理 (并发={self.max_workers})...")
        valid = []
        tested = 0

        def _test_one(proxy_info):
            proxy_url = f"http://{proxy_info['host']}:{proxy_info['port']}"
            try:
                r = requests.get(
                    self.validate_url,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=self.validate_timeout,
                    allow_redirects=True,
                )
                return proxy_url if r.status_code == 200 else None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(_test_one, p): p for p in proxies}
            for future in as_completed(futures):
                tested += 1
                result = future.result()
                if result:
                    valid.append(result)
                    _log(f"  [{tested}/{len(proxies)}] [OK] {result}")
                # 不打印失败的，太多了

        self._valid_proxies = valid
        _log(f"验证完成: {len(valid)}/{len(proxies)} 个代理可用")
        return valid

    def fetch_and_validate(self) -> List[str]:
        """抓取并验证，返回可用代理列表"""
        self.fetch()
        return self.validate()

    def save_to_file(self, file_path: str, proxies: Optional[List[str]] = None) -> int:
        """将代理列表保存到文件"""
        if proxies is None:
            proxies = self._valid_proxies

        if not proxies:
            _log("没有可用代理可保存", "WARN")
            return 0

        import os
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# 自动抓取的免费代理 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# 来源: {self.source_url}\n")
            f.write(f"# 数量: {len(proxies)}\n\n")
            for proxy in proxies:
                f.write(proxy + "\n")

        _log(f"已保存 {len(proxies)} 个代理到 {file_path}")
        return len(proxies)


# ==================== 便捷函数 ====================

def fetch_and_save(
    file_path: str,
    min_count: int = DEFAULT_MIN_PROXIES,
    validate_url: str = DEFAULT_VALIDATE_URL,
    validate_timeout: int = DEFAULT_VALIDATE_TIMEOUT,
) -> List[str]:
    """
    一键抓取免费代理并保存到文件

    参数:
        file_path: 保存路径
        min_count: 最少需要的代理数量，不足则警告
        validate_url: 验证用的 URL
        validate_timeout: 验证超时(秒)

    返回:
        可用代理列表
    """
    fetcher = FreeProxyFetcher(
        validate_url=validate_url,
        validate_timeout=validate_timeout,
    )
    proxies = fetcher.fetch_and_validate()

    if len(proxies) < min_count:
        _log(f"可用代理数量 ({len(proxies)}) 少于最低要求 ({min_count})", "WARN")
        if not proxies:
            _log("未能获取到任何可用代理，请检查网络连接", "ERROR")
            return []

    fetcher.save_to_file(file_path, proxies)
    return proxies
