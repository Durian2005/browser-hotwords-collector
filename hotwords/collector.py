"""采集层：只负责"拿到原始数据"，不做解析。

Fetcher 适配策略（V1）：
  优先使用 Scrapling 的 Fetcher（curl_cffi，TLS 指纹伪装 + 统一 API）。
  若未安装 scrapling，自动降级到 httpx / requests —— 保证骨架开箱可跑。
  保留 type=dynamic 的位置，将来可切 DynamicFetcher，解析层无感。

重试策略：
  指数退避（base * 2^n），最多 max_attempts 次；
  仅对超时/连接错误/指定状态码重试，403/401 直接放弃（重试只会加重风控）。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from hotwords.config import SourceConfig

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BaseFetcher:
    """统一 fetch(url, headers, timeout) -> (status, body_text)。"""

    name = "base"

    def fetch(self, url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, str]:
        raise NotImplementedError


def ca_bundle_arg() -> bool | str:
    """给 curl_cffi 的 verify 参数算出可用的 CA 路径。

    ⚠️ Windows 上的真实坑（实测 2026-10-11）：
      libcurl 打不开【非 ASCII 路径】的 CA 文件。本项目正好落在一个中文目录，
      于是 certifi 默认给出的
          E:\\<中文目录>\\.venv\\Lib\\site-packages\\certifi\\cacert.pem
      会直接失败：
          curl: (77) error adding trust anchors from locations: CAfile: ...
      而同一份 pem 换到纯 ASCII 路径（C:\\Temp\\cacert.pem）就 200。
      交叉验证过：ASCII 路径 OK / 默认中文路径 FAIL / 其余条件完全相同。

    处理：路径含非 ASCII 时，把 pem 复制到 %LOCALAPPDATA%（通常为 ASCII）再引用。
    注意【绝不】退回 verify=False —— 那等于关掉证书校验，是安全倒退；
    复制失败时宁可沿用原路径（大不了请求失败），也不降低校验强度。
    """
    import certifi
    from pathlib import Path

    pem = Path(certifi.where())
    if str(pem).isascii():
        return True  # 路径本身没问题，交回 curl_cffi 默认处理

    import os
    import shutil
    import tempfile

    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    if not str(base).isascii():
        return True  # 兜底目录也是非 ASCII，无能为力，不降级校验

    dst = Path(base) / "hotwords" / "cacert.pem"
    try:
        if not dst.exists() or dst.stat().st_size != pem.stat().st_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pem, dst)
        return str(dst)
    except OSError as e:  # 复制失败：保持原样，请求可能失败但不降级
        logger.warning("CA 复制到 ASCII 路径失败（%s），沿用原路径", e)
        return True


class CurlCffiFetcher(BaseFetcher):
    """curl_cffi：TLS/HTTP2 指纹伪装，仅几 MB，无需浏览器。

    这是"轻量版 TLS 指纹"档位 —— 具备和 Scrapling Fetcher 同源的底层能力
    （Scrapling 的 Fetcher 底层正是 curl_cffi），但不带 Scrapling 的额外依赖。
    适合只需指纹伪装、不需要浏览器渲染的场景。
    """

    name = "curl_cffi"

    def __init__(self, impersonate: str = "chrome"):
        from curl_cffi import requests as _req

        self._req = _req
        self._impersonate = impersonate
        self._verify = ca_bundle_arg()

    def fetch(self, url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, str]:
        resp = self._req.get(
            url,
            headers=headers,
            impersonate=self._impersonate,
            timeout=timeout,
            verify=self._verify,
        )
        return resp.status_code, resp.text


class ScraplingFetcher(BaseFetcher):
    """Scrapling Fetcher：TLS 指纹伪装（impersonate）。"""

    name = "scrapling"

    def __init__(self, impersonate: str = "chrome"):
        from scrapling.fetchers import Fetcher  # 延迟导入

        self._Fetcher = Fetcher
        self._impersonate = impersonate

    def fetch(self, url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, str]:
        page = self._Fetcher.get(
            url,
            headers=headers,
            impersonate=self._impersonate,
            timeout=timeout,
        )
        return page.status, page.html_content


class HttpxFetcher(BaseFetcher):
    """降级方案：httpx（无 TLS 指纹伪装，但足够跑通 JSON 接口）。"""

    name = "httpx"

    def fetch(self, url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, str]:
        import httpx

        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        return resp.status_code, resp.text


class RequestsFetcher(BaseFetcher):
    """最后兜底：requests。"""

    name = "requests"

    def fetch(self, url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, str]:
        import requests

        resp = requests.get(url, headers=headers, timeout=timeout)
        return resp.status_code, resp.text


def build_fetcher(prefer: str = "auto") -> BaseFetcher:
    """按优先级挑选可用 Fetcher。

    优先级（auto）：scrapling > curl_cffi > httpx > requests
      - scrapling：完整框架，TLS 指纹 + 浏览器能力（依赖重）
      - curl_cffi：轻量 TLS 指纹，几 MB，无浏览器（大多数场景够用）
      - httpx/requests：无指纹伪装，纯兜底
    """
    order = (
        ["scrapling", "curl_cffi", "httpx", "requests"]
        if prefer == "auto"
        else [prefer, "curl_cffi", "httpx", "requests"]
    )
    errors: list[str] = []
    for name in order:
        try:
            if name == "scrapling":
                return ScraplingFetcher()
            if name == "curl_cffi":
                return CurlCffiFetcher()
            if name == "httpx":
                return HttpxFetcher()
            if name == "requests":
                return RequestsFetcher()
        except ImportError as e:  # 未安装则试下一个
            errors.append(f"{name}: {e}")
            continue
    raise RuntimeError("无可用 Fetcher：" + "; ".join(errors))


def fetch_with_retry(
    fetcher: BaseFetcher,
    url: str,
    headers: dict[str, str],
    retry_cfg: Any,
    timeout: int = 15,
) -> tuple[int, str]:
    """带指数退避的抓取。"""
    max_attempts = getattr(retry_cfg, "max_attempts", 3)
    base = getattr(retry_cfg, "backoff_base", 1.0)
    retry_status = set(getattr(retry_cfg, "retry_on_status", [429, 500, 502, 503, 504]))

    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            status, body = fetcher.fetch(url, headers, timeout)
            if status == 200:
                return status, body
            if status in retry_status and attempt < max_attempts:
                wait = base * (2 ** (attempt - 1))
                logger.warning("HTTP %s on %s，%.1fs 后重试 (%d/%d)",
                               status, url, wait, attempt, max_attempts)
                time.sleep(wait)
                continue
            # 403/401 等：不重试，直接抛出（重试只会加重风控）
            raise FetchError(f"HTTP {status}", status=status)
        except FetchError:
            raise
        except Exception as e:  # 超时/连接错误 -> 重试
            last_err = e
            if attempt < max_attempts:
                wait = base * (2 ** (attempt - 1))
                logger.warning("请求异常 %s，%.1fs 后重试 (%d/%d): %s",
                               url, wait, attempt, max_attempts, e)
                time.sleep(wait)
            else:
                break
    raise FetchError(f"重试 {max_attempts} 次后仍失败: {last_err}")
