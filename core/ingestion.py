import logging
import re
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class Ingestor:
    def __init__(self, sources):
        self.sources = sources

    def fetch_all(self):
        results = []
        for src in self.sources:
            name = src.get("name", "Source")
            url = src.get("url")
            stype = src.get("type", "rss")
            try:
                if stype == "rss":
                    results.extend(self._fetch_rss(name, url))
                elif stype == "html" and src.get("parser") == "sina_finance":
                    results.extend(self._fetch_sina_finance(name, url))
                else:
                    logger.warning("未知來源類型: %s %s", stype, name)
            except Exception as e:
                logger.error("抓取 %s 失敗: %s", name, e)
        return results

    def _fetch_rss(self, name, url):
        items = []
        timeout = httpx.Timeout(30.0, connect=15.0)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            feed = feedparser.parse(r.text)
        for entry in feed.entries:
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            summary = ""
            if getattr(entry, "summary", None):
                summary = BeautifulSoup(entry.summary, "html.parser").get_text(" ", strip=True)
            elif getattr(entry, "description", None):
                summary = BeautifulSoup(entry.description, "html.parser").get_text(" ", strip=True)
            published = ""
            if getattr(entry, "published", None):
                published = entry.published
            elif getattr(entry, "updated", None):
                published = entry.updated
            if not link:
                continue
            items.append(
                {
                    "source": name,
                    "title": title.strip(),
                    "link": link.strip(),
                    "summary": summary[:5000],
                    "published": published,
                }
            )
        return items

    def _fetch_sina_finance(self, name, url):
        items = []
        timeout = httpx.Timeout(30.0, connect=15.0)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            # httpx.Response 沒有 apparent_encoding 屬性，避免本機更新時噴錯。
            r.encoding = "utf-8"
            html = r.text
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if "finance.sina.com.cn" not in href:
                continue
            if not re.search(r"/stock/usstock|/usstock/", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            link = href if href.startswith("http") else urljoin(url, href)
            items.append(
                {
                    "source": name,
                    "title": title[:500],
                    "link": link,
                    "summary": "",
                    "published": "",
                }
            )
        return items[:80]
