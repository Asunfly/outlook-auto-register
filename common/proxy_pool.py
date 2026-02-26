"""
IP 代理池通用模块 (增强版)

支持四种代理方式:
  1. 常规代理 — HTTP/HTTPS/SOCKS5 静态代理列表或 API
  2. 免费代理自动抓取 — 从 free-proxy-list.net 自动抓取并验证
  3. Mihomo 本地代理 — 通过本地 Mihomo 控制端口切换节点
  4. Mihomo 远程代理 — 通过远程 Mihomo API 切换节点

本模块只负责: 管理代理池 → 提供可用代理 → 标记失败代理 → 自动切换节点
具体的代理使用逻辑由调用方决定。

用法:
    from proxy_pool import ProxyPool

    # 方式1: 常规代理（从文件加载）
    pool = ProxyPool.from_file("data/proxies.txt")

    # 方式2: 免费代理自动抓取
    pool = ProxyPool.from_free_proxy(save_path="data/proxies.txt")

    # 方式3: Mihomo 本地代理
    pool = ProxyPool.from_mihomo_local(
        control_url="http://127.0.0.1:9090",
        secret="your_secret",
        proxy_group="PROXY"  # 代理组名称
    )

    # 方式4: Mihomo 远程代理
    pool = ProxyPool.from_mihomo_remote(
        control_url="http://remote-server:9090",
        secret="your_secret",
        proxy_group="PROXY"
    )

    # 获取代理
    proxy = pool.get_proxy()

    # 标记失败（自动切换节点）
    pool.mark_failed(proxy)
"""

import time
import random
import requests
from datetime import datetime
from typing import List, Optional, Dict, Callable


# ==================== 默认配置 ====================

DEFAULT_CHECK_URL = "https://www.google.com"
DEFAULT_CHECK_TIMEOUT = 10
DEFAULT_MAX_FAILURES = 3
DEFAULT_RETRY_INTERVAL = 300
DEFAULT_MIHOMO_PORT = 7890  # Mihomo 默认代理端口


def _log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] [Proxy] {msg}", flush=True)


# ==================== Mihomo 控制器 ====================

class MihomoController:
    """
    Mihomo (Clash Meta) 控制器

    通过 RESTful API 控制 Mihomo 切换节点
    """

    def __init__(self, control_url: str, secret: str = "", proxy_port: int = DEFAULT_MIHOMO_PORT):
        """
        初始化 Mihomo 控制器

        参数:
            control_url: Mihomo 控制端口地址，如 http://127.0.0.1:9090
            secret: Mihomo Secret (如果配置了)
            proxy_port: Mihomo 代理端口，默认 7890
        """
        self.control_url = control_url.rstrip("/")
        self.secret = secret
        self.proxy_port = proxy_port
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else {}

    def get_proxy_groups(self) -> List[Dict]:
        """获取所有代理组"""
        try:
            r = requests.get(f"{self.control_url}/proxies", headers=self.headers, timeout=5)
            r.raise_for_status()
            data = r.json()
            # 过滤出代理组（type 为 Selector, URLTest, Fallback 等）
            groups = []
            for name, info in data.get("proxies", {}).items():
                if info.get("type") in ["Selector", "URLTest", "Fallback", "LoadBalance"]:
                    groups.append({
                        "name": name,
                        "type": info.get("type"),
                        "now": info.get("now"),  # 当前选中的节点
                        "all": info.get("all", []),  # 所有可用节点
                    })
            return groups
        except Exception as e:
            _log(f"获取代理组失败: {e}", "ERROR")
            return []

    def get_group_nodes(self, group_name: str) -> List[str]:
        """获取指定代理组的所有节点"""
        try:
            r = requests.get(f"{self.control_url}/proxies/{group_name}", headers=self.headers, timeout=5)
            r.raise_for_status()
            data = r.json()
            return data.get("all", [])
        except Exception as e:
            _log(f"获取代理组 {group_name} 节点失败: {e}", "ERROR")
            return []

    def get_current_node(self, group_name: str) -> Optional[str]:
        """获取指定代理组当前选中的节点"""
        try:
            r = requests.get(f"{self.control_url}/proxies/{group_name}", headers=self.headers, timeout=5)
            r.raise_for_status()
            data = r.json()
            return data.get("now")
        except Exception as e:
            _log(f"获取当前节点失败: {e}", "ERROR")
            return None

    def switch_node(self, group_name: str, node_name: str) -> bool:
        """切换代理组到指定节点"""
        try:
            r = requests.put(
                f"{self.control_url}/proxies/{group_name}",
                headers=self.headers,
                json={"name": node_name},
                timeout=5
            )
            r.raise_for_status()
            _log(f"切换节点成功: {group_name} -> {node_name}")
            return True
        except Exception as e:
            _log(f"切换节点失败: {e}", "ERROR")
            return False

    def get_proxy_url(self) -> str:
        """获取代理 URL"""
        return f"http://127.0.0.1:{self.proxy_port}"


# ==================== 代理池类 ====================

class ProxyPool:
    """
    IP 代理池管理器 (增强版)

    支持三种代理方式:
    1. 常规代理 — 静态列表或 API
    2. Mihomo 本地代理 — 本地 Mihomo 控制
    3. Mihomo 远程代理 — 远程 Mihomo 控制
    """

    def __init__(
        self,
        proxies: Optional[List[str]] = None,
        api_url: Optional[str] = None,
        api_params: Optional[Dict] = None,
        api_extractor: Optional[Callable] = None,
        mihomo_controller: Optional[MihomoController] = None,
        mihomo_group: Optional[str] = None,
        strategy: str = "random",
        max_failures: int = DEFAULT_MAX_FAILURES,
        retry_interval: int = DEFAULT_RETRY_INTERVAL,
        check_url: str = DEFAULT_CHECK_URL,
        check_timeout: int = DEFAULT_CHECK_TIMEOUT,
        auto_check: bool = False,
        auto_switch: bool = True,  # 失败时自动切换节点（仅 Mihomo）
    ):
        """
        初始化代理池

        参数:
            proxies: 常规代理列表
            api_url: 代理 API 地址
            api_params: API 请求参数
            api_extractor: API 响应提取函数
            mihomo_controller: Mihomo 控制器实例
            mihomo_group: Mihomo 代理组名称
            strategy: 轮换策略 (random/sequential/least_used)
            max_failures: 最大失败次数
            retry_interval: 失败代理重试间隔(秒)
            check_url: 健康检查 URL
            check_timeout: 健康检查超时(秒)
            auto_check: 是否自动健康检查
            auto_switch: 失败时自动切换节点（仅 Mihomo）
        """
        self.proxies = proxies or []
        self.api_url = api_url
        self.api_params = api_params or {}
        self.api_extractor = api_extractor or self._default_api_extractor
        self.mihomo_controller = mihomo_controller
        self.mihomo_group = mihomo_group
        self.strategy = strategy
        self.max_failures = max_failures
        self.retry_interval = retry_interval
        self.check_url = check_url
        self.check_timeout = check_timeout
        self.auto_check = auto_check
        self.auto_switch = auto_switch

        # 代理模式
        self.mode = "mihomo" if mihomo_controller else "normal"

        # 代理状态跟踪
        self.proxy_stats = {}
        self.current_index = 0

        # Mihomo 节点列表
        self.mihomo_nodes = []
        self.current_mihomo_node = None

        # 初始化
        if self.mode == "mihomo":
            self._init_mihomo()
        else:
            for proxy in self.proxies:
                self._init_proxy_stats(proxy)

        _log(f"代理池初始化完成: 模式={self.mode}, "
             f"{'节点数=' + str(len(self.mihomo_nodes)) if self.mode == 'mihomo' else '代理数=' + str(len(self.proxies))}")

    @classmethod
    def from_free_proxy(cls, save_path: str = None, min_count: int = 3,
                        validate_url: str = "https://www.google.com", **kwargs):
        """
        从免费代理源自动抓取并创建代理池

        参数:
            save_path: 保存代理列表的文件路径（可选，保存后可复用）
            min_count: 最少需要的代理数量
            validate_url: 验证代理可用性的 URL
        """
        from free_proxy_fetcher import FreeProxyFetcher

        fetcher = FreeProxyFetcher(validate_url=validate_url)
        proxies = fetcher.fetch_and_validate()

        if len(proxies) < min_count:
            _log(f"可用代理数量 ({len(proxies)}) 少于最低要求 ({min_count})", "WARN")

        if save_path and proxies:
            fetcher.save_to_file(save_path, proxies)

        if not proxies:
            _log("未获取到可用免费代理", "ERROR")
            return cls(**kwargs)

        return cls(proxies=proxies, **kwargs)

    @classmethod
    def from_file(cls, file_path: str, **kwargs):
        """从文件加载常规代理列表"""
        proxies = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxies.append(line)
            _log(f"从文件加载 {len(proxies)} 个代理: {file_path}")
        except Exception as e:
            _log(f"加载代理文件失败: {e}", "ERROR")

        return cls(proxies=proxies, **kwargs)

    @classmethod
    def from_files(cls, file_paths: list, **kwargs):
        """从多个文件合并加载代理列表（自动去重，保持顺序）"""
        import os
        seen = set()
        proxies = []
        for fp in file_paths:
            if not os.path.exists(fp):
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and line not in seen:
                            seen.add(line)
                            proxies.append(line)
                _log(f"从文件加载代理: {fp}")
            except Exception as e:
                _log(f"加载代理文件失败 {fp}: {e}", "ERROR")

        _log(f"合并加载 {len(proxies)} 个代理（来自 {len(file_paths)} 个文件）")
        return cls(proxies=proxies, **kwargs)

    @classmethod
    def from_mihomo_local(cls, control_url: str = "http://127.0.0.1:9090", secret: str = "",
                          proxy_group: str = "PROXY", proxy_port: int = DEFAULT_MIHOMO_PORT, **kwargs):
        """创建 Mihomo 本地代理池"""
        controller = MihomoController(control_url, secret, proxy_port)
        return cls(mihomo_controller=controller, mihomo_group=proxy_group, **kwargs)

    @classmethod
    def from_mihomo_remote(cls, control_url: str, secret: str = "",
                           proxy_group: str = "PROXY", proxy_port: int = DEFAULT_MIHOMO_PORT, **kwargs):
        """创建 Mihomo 远程代理池"""
        controller = MihomoController(control_url, secret, proxy_port)
        return cls(mihomo_controller=controller, mihomo_group=proxy_group, **kwargs)

    def _init_mihomo(self):
        """初始化 Mihomo 节点列表"""
        if not self.mihomo_controller or not self.mihomo_group:
            return

        self.mihomo_nodes = self.mihomo_controller.get_group_nodes(self.mihomo_group)
        self.current_mihomo_node = self.mihomo_controller.get_current_node(self.mihomo_group)

        if not self.mihomo_nodes:
            _log(f"未找到代理组 {self.mihomo_group} 的节点", "ERROR")
        else:
            _log(f"Mihomo 代理组 {self.mihomo_group}: {len(self.mihomo_nodes)} 个节点, 当前={self.current_mihomo_node}")

        # 初始化节点统计
        for node in self.mihomo_nodes:
            self._init_proxy_stats(node)

    def _init_proxy_stats(self, proxy: str):
        """初始化代理统计信息"""
        if proxy not in self.proxy_stats:
            self.proxy_stats[proxy] = {
                "failures": 0,
                "successes": 0,
                "last_used": 0,
                "last_failed": 0,
            }

    def _default_api_extractor(self, response_json):
        """默认的 API 响应提取函数"""
        if isinstance(response_json, str):
            return response_json
        if isinstance(response_json, dict):
            for key in ["proxy", "ip", "data", "result"]:
                if key in response_json:
                    val = response_json[key]
                    if isinstance(val, str):
                        return val
                    if isinstance(val, dict) and "proxy" in val:
                        return val["proxy"]
        return None

    def _fetch_from_api(self) -> Optional[str]:
        """从 API 获取一个新代理"""
        if not self.api_url:
            return None

        try:
            _log(f"从 API 获取代理: {self.api_url}")
            r = requests.get(self.api_url, params=self.api_params, timeout=10)
            r.raise_for_status()

            proxy = self.api_extractor(r.json())
            if proxy:
                _log(f"API 返回代理: {proxy}")
                if proxy not in self.proxies:
                    self.proxies.append(proxy)
                    self._init_proxy_stats(proxy)
                return proxy
            else:
                _log("API 响应中未找到代理", "WARN")
                return None
        except Exception as e:
            _log(f"API 获取代理失败: {e}", "ERROR")
            return None

    def _check_proxy(self, proxy: str) -> bool:
        """健康检查：测试代理是否可用"""
        try:
            proxies = {"http": proxy, "https": proxy}
            r = requests.get(
                self.check_url,
                proxies=proxies,
                timeout=self.check_timeout,
                allow_redirects=True,
            )
            return r.status_code == 200
        except Exception:
            return False

    def _is_available(self, proxy: str) -> bool:
        """检查代理是否可用"""
        stats = self.proxy_stats.get(proxy, {})

        if stats.get("failures", 0) >= self.max_failures:
            last_failed = stats.get("last_failed", 0)
            if time.time() - last_failed < self.retry_interval:
                return False
            stats["failures"] = 0

        return True

    def _switch_mihomo_node(self) -> bool:
        """切换到下一个可用的 Mihomo 节点"""
        if not self.mihomo_controller or not self.mihomo_group:
            return False

        available_nodes = [n for n in self.mihomo_nodes if self._is_available(n) and n != self.current_mihomo_node]

        if not available_nodes:
            _log("没有可用的 Mihomo 节点", "ERROR")
            return False

        # 根据策略选择节点
        if self.strategy == "random":
            next_node = random.choice(available_nodes)
        elif self.strategy == "sequential":
            next_node = available_nodes[self.current_index % len(available_nodes)]
            self.current_index += 1
        elif self.strategy == "least_used":
            next_node = min(available_nodes, key=lambda n: self.proxy_stats[n]["last_used"])
        else:
            next_node = available_nodes[0]

        # 切换节点
        if self.mihomo_controller.switch_node(self.mihomo_group, next_node):
            self.current_mihomo_node = next_node
            self.proxy_stats[next_node]["last_used"] = time.time()
            return True
        else:
            return False

    def get_proxy(self) -> Optional[str]:
        """获取一个可用代理"""
        if self.mode == "mihomo":
            # Mihomo 模式：返回固定的代理地址，节点切换由控制器管理
            if not self.current_mihomo_node:
                self.current_mihomo_node = self.mihomo_controller.get_current_node(self.mihomo_group)

            proxy_url = self.mihomo_controller.get_proxy_url()
            _log(f"使用 Mihomo 代理: {proxy_url} (节点: {self.current_mihomo_node})")
            return proxy_url

        else:
            # 常规模式：从代理列表中选择
            available = [p for p in self.proxies if self._is_available(p)]

            if not available:
                _log("静态代理池已耗尽，尝试从 API 获取", "WARN")
                api_proxy = self._fetch_from_api()
                if api_proxy:
                    available = [api_proxy]
                else:
                    _log("无可用代理", "ERROR")
                    return None

            # 根据策略选择代理
            if self.strategy == "random":
                proxy = random.choice(available)
            elif self.strategy == "sequential":
                proxy = available[self.current_index % len(available)]
                self.current_index += 1
            elif self.strategy == "least_used":
                proxy = min(available, key=lambda p: self.proxy_stats[p]["last_used"])
            else:
                proxy = available[0]

            # 自动健康检查
            if self.auto_check:
                _log(f"健康检查代理: {proxy}")
                if not self._check_proxy(proxy):
                    _log(f"代理不可用: {proxy}", "WARN")
                    self.mark_failed(proxy)
                    return self.get_proxy()

            self.proxy_stats[proxy]["last_used"] = time.time()
            _log(f"使用代理: {proxy}")
            return proxy

    def mark_failed(self, proxy: str):
        """标记代理失败"""
        if self.mode == "mihomo":
            # Mihomo 模式：标记当前节点失败
            if self.current_mihomo_node:
                node = self.current_mihomo_node
                if node not in self.proxy_stats:
                    self._init_proxy_stats(node)

                self.proxy_stats[node]["failures"] += 1
                self.proxy_stats[node]["last_failed"] = time.time()

                failures = self.proxy_stats[node]["failures"]
                _log(f"Mihomo 节点失败 ({failures}/{self.max_failures}): {node}", "WARN")

                # 自动切换节点
                if self.auto_switch and failures >= self.max_failures:
                    _log(f"节点 {node} 已达最大失败次数，自动切换", "WARN")
                    self._switch_mihomo_node()
        else:
            # 常规模式
            if proxy not in self.proxy_stats:
                self._init_proxy_stats(proxy)

            self.proxy_stats[proxy]["failures"] += 1
            self.proxy_stats[proxy]["last_failed"] = time.time()

            failures = self.proxy_stats[proxy]["failures"]
            _log(f"代理失败 ({failures}/{self.max_failures}): {proxy}", "WARN")

    def mark_success(self, proxy: str):
        """标记代理成功"""
        if self.mode == "mihomo":
            if self.current_mihomo_node:
                node = self.current_mihomo_node
                if node not in self.proxy_stats:
                    self._init_proxy_stats(node)
                self.proxy_stats[node]["successes"] += 1
                self.proxy_stats[node]["failures"] = 0
        else:
            if proxy not in self.proxy_stats:
                self._init_proxy_stats(proxy)
            self.proxy_stats[proxy]["successes"] += 1
            self.proxy_stats[proxy]["failures"] = 0

    def switch_node(self, node_name: Optional[str] = None) -> bool:
        """
        手动切换 Mihomo 节点

        参数:
            node_name: 节点名称，如果为 None 则自动选择下一个可用节点

        返回: 是否切换成功
        """
        if self.mode != "mihomo":
            _log("非 Mihomo 模式，无法切换节点", "WARN")
            return False

        if node_name:
            # 切换到指定节点
            if self.mihomo_controller.switch_node(self.mihomo_group, node_name):
                self.current_mihomo_node = node_name
                return True
            else:
                return False
        else:
            # 自动切换到下一个节点
            return self._switch_mihomo_node()

    def get_stats(self) -> Dict:
        """获取代理池统计信息"""
        if self.mode == "mihomo":
            total = len(self.mihomo_nodes)
            available = len([n for n in self.mihomo_nodes if self._is_available(n)])
            failed = total - available

            return {
                "mode": "mihomo",
                "total": total,
                "available": available,
                "failed": failed,
                "current_node": self.current_mihomo_node,
                "proxy_group": self.mihomo_group,
                "details": self.proxy_stats,
            }
        else:
            total = len(self.proxies)
            available = len([p for p in self.proxies if self._is_available(p)])
            failed = total - available

            return {
                "mode": "normal",
                "total": total,
                "available": available,
                "failed": failed,
                "api_enabled": bool(self.api_url),
                "details": self.proxy_stats,
            }

    def print_stats(self):
        """打印代理池统计信息"""
        stats = self.get_stats()
        _log(f"代理池状态: 模式={stats['mode']}, 总数={stats['total']}, 可用={stats['available']}, 失败={stats['failed']}")

        if stats["mode"] == "mihomo":
            _log(f"  当前节点: {stats['current_node']}, 代理组: {stats['proxy_group']}")

        for item, detail in stats["details"].items():
            status = "可用" if self._is_available(item) else "失败"
            _log(f"  {item}: {status}, 成功={detail['successes']}, 失败={detail['failures']}")


# ==================== 便捷函数 ====================

def create_proxy_pool_from_env():
    """
    从环境变量创建代理池

    支持的环境变量:
    - PROXY_MODE: 代理模式 (normal/free_proxy/mihomo_local/mihomo_remote)
    - PROXY_LIST: 逗号分隔的代理列表
    - PROXY_FILE: 代理文件路径
    - PROXY_API_URL: 代理 API 地址
    - PROXY_API_KEY: 代理 API 密钥
    - MIHOMO_CONTROL_URL: Mihomo 控制地址
    - MIHOMO_SECRET: Mihomo Secret
    - MIHOMO_GROUP: Mihomo 代理组名称
    - MIHOMO_PORT: Mihomo 代理端口
    """
    import os

    mode = os.environ.get("PROXY_MODE", "normal")

    if mode == "free_proxy":
        save_path = os.environ.get("PROXY_FILE", None)
        return ProxyPool.from_free_proxy(save_path=save_path)

    elif mode == "mihomo_local":
        control_url = os.environ.get("MIHOMO_CONTROL_URL", "http://127.0.0.1:9090")
        secret = os.environ.get("MIHOMO_SECRET", "")
        group = os.environ.get("MIHOMO_GROUP", "PROXY")
        port = int(os.environ.get("MIHOMO_PORT", DEFAULT_MIHOMO_PORT))
        return ProxyPool.from_mihomo_local(control_url, secret, group, port)

    elif mode == "mihomo_remote":
        control_url = os.environ.get("MIHOMO_CONTROL_URL")
        if not control_url:
            _log("MIHOMO_CONTROL_URL 未配置", "ERROR")
            return ProxyPool()
        secret = os.environ.get("MIHOMO_SECRET", "")
        group = os.environ.get("MIHOMO_GROUP", "PROXY")
        port = int(os.environ.get("MIHOMO_PORT", DEFAULT_MIHOMO_PORT))
        return ProxyPool.from_mihomo_remote(control_url, secret, group, port)

    else:  # normal
        proxy_list = os.environ.get("PROXY_LIST")
        proxy_file = os.environ.get("PROXY_FILE")
        api_url = os.environ.get("PROXY_API_URL")
        api_key = os.environ.get("PROXY_API_KEY")

        if proxy_file:
            return ProxyPool.from_file(proxy_file)
        elif proxy_list:
            proxies = [p.strip() for p in proxy_list.split(",") if p.strip()]
            return ProxyPool(proxies=proxies)
        elif api_url:
            api_params = {"key": api_key} if api_key else {}
            return ProxyPool(api_url=api_url, api_params=api_params)
        else:
            _log("未配置代理池环境变量", "WARN")
            return ProxyPool()
