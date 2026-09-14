"""Keyless direct-fetch web extract provider -- Paspartu user plugin.

Guarantees ``web_extract`` is never dead: fetches a URL over plain HTTP(S)
and converts the HTML body to readable text. No API key, no third-party
service, no account. Search is NOT supported -- pair with a search backend
such as ``ddgs``.

Install to ~/.hermes/plugins/web_direct/ and enable via config:
    plugins.enabled: [..., web_direct]
    web.extract_backend: direct
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Wikimedia and some other sites reject generic browser UAs from datacenter
# IPs but allow a descriptive, self-identifying one.
_UA_POLITE = (
    "PaspartuBot/1.0 (https://github.com/AML1969/Paspartu) python-httpx"
)

# Tags whose text content is never part of the document body.
_DROP_TAGS = (
    "script", "style", "noscript", "template", "svg", "canvas",
    "nav", "header", "footer", "aside", "form", "iframe", "button",
)

_MAX_BYTES = 5_000_000
_DEFAULT_TIMEOUT = 30


class DirectWebExtractProvider(WebSearchProvider):
    """Plain HTTP fetch + HTML-to-text. No API key required."""

    @property
    def name(self) -> str:
        return "direct"

    @property
    def display_name(self) -> str:
        return "Direct fetch (keyless)"

    def is_available(self) -> bool:
        """True when httpx is importable. Must not do network I/O."""
        try:
            import httpx  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return {
            "success": False,
            "error": (
                "The 'direct' backend is extract-only and cannot search. "
                "Set web.search_backend to ddgs, brave_free, tavily, exa "
                "or parallel."
            ),
        }

    # ------------------------------------------------------------------
    # extraction
    # ------------------------------------------------------------------

    def extract(self, urls: List[str], **kwargs: Any) -> Any:
        """Fetch each URL and return normalized documents.

        Failures become documents carrying an ``error`` field rather than
        raising, so one bad URL never sinks the whole batch.
        """
        if isinstance(urls, str):
            urls = [urls]

        timeout = kwargs.get("timeout", _DEFAULT_TIMEOUT)
        documents: List[Dict[str, Any]] = []

        for url in urls:
            documents.append(self._extract_one(url, timeout))

        return documents

    def _extract_one(self, url: str, timeout: int) -> Dict[str, Any]:
        import httpx

        def _headers(ua: str) -> Dict[str, str]:
            return {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru,en;q=0.9",
            }

        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=timeout,
                headers=_headers(_UA),
            ) as client:
                response = client.get(url)
                # Some sites (notably Wikimedia) reject generic browser UAs
                # from datacenter IPs; retry once identifying ourselves.
                if response.status_code in (401, 403, 429):
                    retry = client.get(url, headers=_headers(_UA_POLITE))
                    if retry.status_code < 400:
                        response = retry
                response.raise_for_status()

                ctype = response.headers.get("content-type", "")
                if "html" not in ctype and "xml" not in ctype:
                    if "text/" in ctype or "json" in ctype:
                        body = response.text[:_MAX_BYTES]
                        return self._document(
                            str(response.url), "", body, status=response.status_code
                        )
                    return self._failure(
                        url,
                        f"Unsupported content-type '{ctype}' "
                        "(not an HTML or text document).",
                    )

                html = response.text[:_MAX_BYTES]
                title, text = self._html_to_text(html)
                if not text.strip():
                    return self._failure(
                        str(response.url),
                        "Page fetched (HTTP %s) but contained no extractable "
                        "text -- it is most likely rendered client-side by "
                        "JavaScript." % response.status_code,
                    )
                return self._document(
                    str(response.url), title, text, status=response.status_code
                )

        except httpx.HTTPStatusError as exc:
            # web-direct-actionable-403: отказ должен быть инструкцией, а не тупиком.
            code = exc.response.status_code
            if code in (401, 403, 429):
                return self._failure(
                    url,
                    f"HTTP {code}: сайт блокирует автоматический доступ "
                    f"(Cloudflare/WAF). Смена User-Agent НЕ помогает — не повторяй "
                    f"этот URL и не пробуй другие страницы того же домена. "
                    f"Возьми эти данные через Perplexity или другой источник.",
                )
            if code == 404:
                return self._failure(
                    url,
                    f"HTTP 404: такой страницы нет. Скорее всего URL собран по "
                    f"догадке — не подставляй адрес сам, найди рабочую ссылку "
                    f"поиском и только потом извлекай.",
                )
            return self._failure(url, f"HTTP {code} fetching the page.")
        except httpx.TimeoutException:
            return self._failure(
                url,
                f"Timed out after {timeout}s. Не повторяй этот URL — возьми "
                f"данные через Perplexity или другой источник.",
            )
        except Exception as exc:  # noqa: BLE001 - never sink the batch
            logger.info("direct extract failed for %s: %s", url, exc)
            return self._failure(url, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _html_to_text(html: str) -> tuple:
        """Return (title, text). Uses bs4 when present, regex otherwise."""
        try:
            from bs4 import BeautifulSoup

            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:  # noqa: BLE001 - lxml optional
                soup = BeautifulSoup(html, "html.parser")

            title = soup.title.get_text(strip=True) if soup.title else ""

            for tag in soup(list(_DROP_TAGS)):
                tag.decompose()

            body = soup.body or soup
            text = body.get_text("\n", strip=True)
        except ImportError:
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", html, re.S | re.I
            )
            title = (
                re.sub(r"\s+", " ", title_match.group(1)).strip()
                if title_match
                else ""
            )
            stripped = html
            for tag in _DROP_TAGS:
                stripped = re.sub(
                    rf"(?is)<{tag}\b.*?</{tag}>", " ", stripped
                )
            stripped = re.sub(r"(?s)<!--.*?-->", " ", stripped)
            text = re.sub(r"<[^>]+>", "\n", stripped)

        text = _unescape(text)
        # collapse runaway blank lines and stray spaces
        text = re.sub(r"[ \t\xa0]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        return title, text.strip()

    @staticmethod
    def _document(
        url: str, title: str, text: str, status: int = 200
    ) -> Dict[str, Any]:
        return {
            "url": url,
            "title": title,
            "content": text,
            "raw_content": text,
            "metadata": {
                "sourceURL": url,
                "title": title,
                "statusCode": status,
                "extractor": "direct",
            },
        }

    @staticmethod
    def _failure(url: str, error: str) -> Dict[str, Any]:
        return {
            "url": url,
            "title": "",
            "content": "",
            "raw_content": "",
            "error": error,
            "metadata": {"sourceURL": url, "extractor": "direct"},
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "requires_api_key": False,
            "fields": [],
            "notes": (
                "No configuration required. Fetches pages directly over "
                "HTTP and converts HTML to text. Cannot render "
                "JavaScript-only pages."
            ),
        }


def _unescape(text: str) -> str:
    import html as _html

    return _html.unescape(text)


def register(ctx) -> None:
    """Register the direct provider with the plugin context."""
    ctx.register_web_search_provider(DirectWebExtractProvider())
