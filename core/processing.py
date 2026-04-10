import difflib
import html
import json
import os
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)
_TW = ZoneInfo("Asia/Taipei")
MAX_TODAY_NEWS_ITEMS = 500
MAX_TREND_POINTS = 1000
SUMMARY_ITEMS_LIMIT = 200
COLLECTION_WINDOW_HOURS = 72
LAST_SUMMARY_FP_FILE = "data/last_summary_fp.txt"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _abs_data(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(_project_root(), path)


def _hydrate_item_dt(item: dict, calendar_date_str: Optional[str]) -> None:
    dt = item.get("_dt")
    if dt is not None and dt != datetime.min:
        return
    pub = item.get("published")
    if pub:
        try:
            import dateutil.parser

            pub_dt = dateutil.parser.parse(pub)
            if pub_dt.tzinfo:
                pub_dt = pub_dt.astimezone(_TW).replace(tzinfo=None)
            else:
                pub_dt = pub_dt.replace(tzinfo=None)
            item["_dt"] = pub_dt
            return
        except Exception:
            pass
    disp = item.get("display_time")
    if disp and calendar_date_str:
        try:
            m = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", disp.strip())
            if m:
                mo, d, ho, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                y = int(calendar_date_str.split("-")[0])
                item["_dt"] = datetime(y, mo, d, ho, mi)
                return
        except Exception:
            pass
    item["_dt"] = datetime.min


def ensure_today_news_line_breaks(summary: str) -> str:
    import re as _re

    start = "#### 今日財經要聞"
    if start not in summary:
        return summary
    si = summary.index(start) + len(start)
    ei = len(summary)
    for em in ("\n#### 核心動態分析", "\n#### 核心動態分析與情緒"):
        pos = summary.find(em, si)
        if pos != -1:
            ei = min(ei, pos)
    body = summary[si:ei]
    body = _re.sub(
        r"(?<=\])\s*(?=\s*(?:\*\*)?\s*\[(?:\d{2}/\d{2}\s+\d{2}:\d{2}|\d{2}:\d{2})\])",
        "\n\n",
        body,
    )
    body = _re.sub(r"(?<!\n)\n(?=\s*\*\*\s*\[)", "\n\n", body)
    body = _re.sub(r"(?<!\n)\n(?=\s*\*\s*\[)", "\n\n", body)
    body = _re.sub(r"(?<!\n)\n(?=\s*\[)", "\n\n", body)
    body = _re.sub(r"\n\n\n+", "\n\n", body)
    return summary[:si] + body + summary[ei:]


def _unwrap_sentiment_spans(body: str) -> str:
    import re as _re

    body = _re.sub(
        r'<span style="color:#EF4444;font-weight:600;">(\[[^\]]+\])</span>',
        r"\1",
        body,
    )
    body = _re.sub(
        r'<span style="color:#48BB78;font-weight:600;">(\[[^\]]+\])</span>',
        r"\1",
        body,
    )
    body = _re.sub(
        r'<span style="color:#A0AEC0;font-weight:600;">(\[[^\]]+\])</span>',
        r"\1",
        body,
    )
    return body


def colorize_sentiment_scores_in_today_news(summary: str) -> str:
    import re as _re

    start = "#### 今日財經要聞"
    if start not in summary:
        return summary
    si = summary.index(start) + len(start)
    ei = len(summary)
    for em in ("\n#### 核心動態分析", "\n#### 核心動態分析與情緒"):
        pos = summary.find(em, si)
        if pos != -1:
            ei = min(ei, pos)
    body = summary[si:ei]
    body = _unwrap_sentiment_spans(body)

    def wrap_red(m: re.Match) -> str:
        return f'<span style="color:#EF4444;font-weight:600;">{m.group(0)}</span>'

    def wrap_green(m: re.Match) -> str:
        return f'<span style="color:#48BB78;font-weight:600;">{m.group(0)}</span>'

    def wrap_gray(m: re.Match) -> str:
        return f'<span style="color:#A0AEC0;font-weight:600;">{m.group(0)}</span>'

    body = _re.sub(r"\[\+\/-[^\]]+\]", wrap_gray, body)
    body = _re.sub(r"\[\+\s*\d+\.?\d*\]", wrap_red, body)
    body = _re.sub(r"\[-\s*\d+\.?\d*\]", wrap_green, body)
    return summary[:si] + body + summary[ei:]


def _parse_item_news_datetime(plain: str, ref_year: int, update_dt: datetime) -> datetime:
    import re as _re

    plain = _re.sub(r"<[^>]+>", "", plain).strip()
    m = _re.search(r"\[(\d{2})/(\d{2})\s+(\d{2}):(\d{2})\]", plain)
    if m:
        mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            return datetime(ref_year, mo, d, h, mi)
        except ValueError:
            return datetime.min
    m = _re.search(r"\[(\d{1,2}):(\d{2})\]", plain)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            return datetime.min
        item_t = datetime(2000, 1, 1, h, mi).time()
        upd_t = update_dt.time()
        ref_d = update_dt.date()
        if item_t <= upd_t:
            d = ref_d
        elif upd_t.hour < 6 and h >= 12:
            d = ref_d - timedelta(days=1)
        else:
            d = ref_d
        return datetime.combine(d, item_t)
    return datetime.min


def sort_today_news_section_newest_first(summary: str) -> str:
    import re as _re

    start = "#### 今日財經要聞"
    if start not in summary:
        return summary

    um = _re.search(r"🕒 更新時間：([\d-]+\s[\d:]+)", summary)
    if um:
        try:
            update_dt = datetime.strptime(um.group(1).strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            update_dt = datetime.now(_TW)
    else:
        update_dt = datetime.now(_TW)

    ref_year = update_dt.year
    si = summary.index(start) + len(start)
    ei = len(summary)
    for em in ("\n#### 核心動態分析", "\n#### 核心動態分析與情緒"):
        pos = summary.find(em, si)
        if pos != -1:
            ei = min(ei, pos)
    body = summary[si:ei]
    stripped = body.strip()
    if not stripped:
        return summary

    raw_chunks = _re.split(r"\n\s*\n+", stripped)
    items = [c.strip() for c in raw_chunks if c.strip()]
    if len(items) <= 1:
        return summary

    keyed = []
    for item in items:
        plain = _re.sub(r"<[^>]+>", "", item)
        dt = _parse_item_news_datetime(plain, ref_year, update_dt)
        keyed.append((dt, item))
    keyed.sort(key=lambda x: x[0], reverse=True)
    new_body = "\n\n".join(x[1] for x in keyed) + "\n\n"
    return summary[:si] + "\n\n" + new_body + summary[ei:]


def filter_today_news_section(summary: str, show_all: bool) -> str:
    import re as _re

    if show_all:
        return summary
    start = "#### 今日財經要聞"
    if start not in summary:
        return summary

    um = _re.search(r"🕒 更新時間：([\d-]+\s[\d:]+)", summary)
    if um:
        try:
            update_dt = datetime.strptime(um.group(1).strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            update_dt = datetime.now(_TW).replace(tzinfo=None)
    else:
        update_dt = datetime.now(_TW).replace(tzinfo=None)

    ref_year = update_dt.year
    now_naive = datetime.now(_TW).replace(tzinfo=None)
    cutoff = now_naive - timedelta(hours=24)

    si = summary.index(start) + len(start)
    ei = len(summary)
    for em in ("\n#### 核心動態分析", "\n#### 核心動態分析與情緒"):
        pos = summary.find(em, si)
        if pos != -1:
            ei = min(ei, pos)
    body = summary[si:ei]
    stripped = body.strip()
    if not stripped:
        return summary

    raw_chunks = _re.split(r"\n\s*\n+", stripped)
    items = [c.strip() for c in raw_chunks if c.strip()]

    kept = []
    for item in items:
        plain = _re.sub(r"<[^>]+>", "", item)
        item_dt = _parse_item_news_datetime(plain, ref_year, update_dt)
        if item_dt == datetime.min:
            kept.append(item)
        elif item_dt >= cutoff:
            kept.append(item)

    if not kept:
        return summary

    new_body = "\n\n".join(kept) + "\n\n"
    return summary[:si] + "\n\n" + new_body + summary[ei:]


def _split_sentiment_tail(rest: str) -> tuple:
    rest = rest or ""
    if "<span" in rest.lower():
        m = re.search(r"(\s*(?:<span[^>]*>.*?</span>\s*)+)$", rest, re.DOTALL | re.IGNORECASE)
        if m:
            return rest[: m.start()].rstrip(), m.group(1)
    m2 = re.search(r"(\s*\[\+\-?\d+(?:\.\d+)?\]\s*)$", rest)
    if m2:
        return rest[: m2.start()].rstrip(), m2.group(1)
    m3 = re.search(r"(\s*\[\+\/[^\]]+\]\s*)$", rest)
    if m3:
        return rest[: m3.start()].rstrip(), m3.group(1)
    return rest.rstrip(), ""


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _match_link_for_chunk(plain: str, news_items: list) -> Optional[str]:
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = re.sub(r"\*\*?", "", plain)
    plain = plain.strip()
    disp = None
    m = re.search(r"\[(\d{2}/\d{2}\s+\d{2}:\d{2})\]", plain)
    if m:
        disp = _norm_ws(m.group(1))
    else:
        m2 = re.search(r"\[(\d{1,2}:\d{2})\]", plain)
        if m2:
            disp = m2.group(1)
    candidates: list = []
    if disp:
        for n in news_items:
            nd = _norm_ws(n.get("display_time") or "")
            if nd == disp:
                candidates.append(n)
    if len(candidates) == 1:
        return candidates[0].get("link")
    if not candidates:
        candidates = list(news_items)
    rest = plain
    m = re.search(r"\[[^\]]+\]\s*", rest)
    if m:
        rest = rest[m.end() :]
    rest = re.sub(r"\s*\[\+\-[^\]]+\]\s*$", "", rest).strip()
    rest = re.sub(r"\s*\[\+\/[^\]]+\]\s*$", "", rest).strip()
    headline = _norm_ws(rest)[:500]
    if len(headline) < 3:
        return None
    best_score = 0.0
    best_url: Optional[str] = None
    for n in candidates:
        t = _norm_ws(n.get("title") or "")[:300]
        if not t:
            continue
        score = difflib.SequenceMatcher(None, headline, t).ratio()
        h35, t35 = headline[:35], t[:35]
        if h35 and t35 and (h35 in t or t35 in headline):
            score = max(score, 0.52)
        if score > best_score:
            best_score = score
            best_url = n.get("link")
    if best_score < 0.24:
        return None
    return best_url


def _wrap_chunk_with_link(chunk: str, url: str) -> str:
    if "news-ext-link" in chunk:
        return chunk
    if re.search(r"<a\s+[^>]*href\s*=", chunk, re.I):
        return chunk
    esc = html.escape(url, quote=True)
    start = end = None
    for m in re.finditer(r"\[[^\]]+\]", chunk):
        inner = m.group(0)[1:-1].strip()
        if re.match(r"\d{2}/\d{2}\s+\d{2}:\d{2}", inner) or re.match(r"^\d{1,2}:\d{2}$", inner):
            start, end = m.start(), m.end()
            break
    if start is None:
        return chunk
    pre = chunk[:start]
    time_token = chunk[start:end]
    rest = chunk[end:]
    head, tail = _split_sentiment_tail(rest)
    if not head.strip():
        return chunk
    head_esc = html.escape(head)
    return (
        f"{pre}{time_token}{head_esc} "
        f'<a href="{esc}" class="news-ext-link" target="_blank" rel="noopener noreferrer" '
        f'title="開啟原文" aria-label="開啟原文連結">🔗</a>{tail}'
    )


def linkify_today_news_section(summary: str, news_items: list) -> str:
    if not news_items:
        return summary
    start = "#### 今日財經要聞"
    if start not in summary:
        return summary
    si = summary.index(start) + len(start)
    ei = len(summary)
    for em in ("\n#### 核心動態分析", "\n#### 核心動態分析與情緒"):
        pos = summary.find(em, si)
        if pos != -1:
            ei = min(ei, pos)
    body = summary[si:ei]
    stripped = body.strip()
    if not stripped:
        return summary
    raw_chunks = re.split(r"\n\s*\n+", stripped)
    new_chunks = []
    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        plain = re.sub(r"<[^>]+>", "", chunk)
        link = _match_link_for_chunk(plain, news_items)
        if not link:
            new_chunks.append(chunk)
            continue
        wrapped = _wrap_chunk_with_link(chunk, link)
        new_chunks.append(wrapped if wrapped else chunk)
    new_body = "\n\n".join(new_chunks) + "\n\n"
    return summary[:si] + "\n\n" + new_body + summary[ei:]


def extract_confidence_index_for_trend(summary: str) -> Optional[float]:
    if not summary:
        return None
    m = re.search(r"####\s*今日市場信心指數\s*", summary)
    if not m:
        matches = list(re.finditer(r"信心指數[：:為\s]*\**(\d+(?:\.\d+)?)\**", summary))
        if matches:
            return float(matches[-1].group(1))
        return None
    start = m.end()
    ei = len(summary)
    for em in ("\n#### ", "\n---"):
        p = summary.find(em, start)
        if p != -1:
            ei = min(ei, p)
    block = summary[start:ei]
    m2 = re.search(r"信心指數[：:為\s]*\**(\d+(?:\.\d+)?)\**", block)
    if m2:
        return float(m2.group(1))
    m3 = re.search(r"[：:]\s*\**(\d+(?:\.\d+)?)\**", block)
    if m3:
        return float(m3.group(1))
    block_plain = re.sub(r"<[^>]+>", "", block)
    m4 = re.search(r"\b(\d{1,3}(?:\.\d+)?)\b", block_plain.strip())
    if m4:
        v = float(m4.group(1))
        if 0 <= v <= 100:
            return v
    return None


def _confidence_to_trend_value(val: float) -> float:
    avg_val = float(val)
    if -1.0 <= avg_val <= 1.0 and avg_val != 0:
        avg_val = round((avg_val + 1) * 50)
    return max(0.0, min(100.0, float(avg_val)))


def average_line_sentiments_to_trend_value(summary: str) -> Optional[float]:
    import re as _re

    start = "#### 今日財經要聞"
    if start not in summary:
        return None
    si = summary.index(start) + len(start)
    ei = len(summary)
    for em in ("\n#### 核心動態分析", "\n#### 核心動態分析與情緒", "\n#### 今日市場信心指數"):
        pos = summary.find(em, si)
        if pos != -1:
            ei = min(ei, pos)
    body = summary[si:ei]
    body = _re.sub(r"<[^>]+>", "", body)
    scores = []
    for m in _re.finditer(r"\[([+-]?\d+(?:\.\d+)?)\]", body):
        try:
            v = float(m.group(1))
            if -1.0 <= v <= 1.0:
                scores.append(v)
        except ValueError:
            continue
    if not scores:
        return None
    mid = sum(scores) / len(scores)
    return max(0.0, min(100.0, round((mid + 1) * 50)))


class Processor:
    def __init__(
        self,
        keywords,
        config,
        history_file="data/processed_hashes.json",
        trend_file="data/sentiment_trends.json",
        today_news_file="data/today_news.json",
        pool_file="data/recent_news_pool.json",
    ):
        self.keywords = keywords
        self.config = config
        self.history_file = _abs_data(history_file)
        self.trend_file = _abs_data(trend_file)
        self.today_news_file = _abs_data(today_news_file)
        self.pool_file = _abs_data(pool_file)
        self._recent_pool_snapshot: Optional[list] = None
        self.history = self.load_history()
        self.today_news = self.load_today_news()

        self.openai_client = None
        self.gemini_client = None
        self._notify_channels = True

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Error loading history: %s", e)
                return []
        return []

    def save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Error saving history: %s", e)

    def load_today_news(self):
        if os.path.exists(self.today_news_file):
            try:
                with open(self.today_news_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    last_update = data.get("date")
                    today_str = datetime.now(_TW).strftime("%Y-%m-%d")
                    if last_update == today_str:
                        news = data.get("news", [])
                        for it in news:
                            _hydrate_item_dt(it, last_update)
                        news.sort(key=lambda x: x.get("_dt", datetime.min), reverse=True)
                        if len(news) > MAX_TODAY_NEWS_ITEMS:
                            logger.info(
                                "today_news 載入後裁切：%d -> %d",
                                len(news),
                                MAX_TODAY_NEWS_ITEMS,
                            )
                            news = news[:MAX_TODAY_NEWS_ITEMS]
                        return news
                    logger.info("檢測到日期變更，重置當日新聞列表。")
                    return []
            except Exception as e:
                logger.error("Error loading today news: %s", e)
                return []
        return []

    def save_today_news(self):
        try:
            today_str = datetime.now(_TW).strftime("%Y-%m-%d")
            self.today_news.sort(key=lambda x: x.get("_dt", datetime.min), reverse=True)
            if len(self.today_news) > MAX_TODAY_NEWS_ITEMS:
                logger.info(
                    "today_news 寫入前裁切：%d -> %d",
                    len(self.today_news),
                    MAX_TODAY_NEWS_ITEMS,
                )
                self.today_news = self.today_news[:MAX_TODAY_NEWS_ITEMS]
            serializable_news = []
            for item in self.today_news:
                clean_item = {k: v for k, v in item.items() if k != "_dt"}
                serializable_news.append(clean_item)

            data = {"date": today_str, "news": serializable_news}
            with open(self.today_news_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Error saving today news: %s", e)

    def load_recent_pool(self) -> list:
        if not os.path.exists(self.pool_file):
            return []
        try:
            with open(self.pool_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data if isinstance(data, list) else data.get("items", [])
            if not isinstance(items, list):
                return []
            cal = datetime.now(_TW).strftime("%Y-%m-%d")
            for it in items:
                if isinstance(it, dict):
                    _hydrate_item_dt(it, cal)
            return items
        except Exception as e:
            logger.warning("讀取 recent_news_pool 失敗：%s", e)
            return []

    def save_recent_pool(self, items: list) -> None:
        try:
            serializable = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                clean_item = {k: v for k, v in item.items() if k != "_dt"}
                serializable.append(clean_item)
            os.makedirs(os.path.dirname(self.pool_file) or ".", exist_ok=True)
            with open(self.pool_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("寫入 recent_news_pool 失敗：%s", e)

    def merge_recent_pool_from_candidates(self, candidates: list) -> None:
        pool = self.load_recent_pool()
        by_link: dict = {}
        for it in pool:
            if isinstance(it, dict) and it.get("link"):
                by_link[it["link"]] = it
        for c in candidates:
            if isinstance(c, dict) and c.get("link"):
                by_link[c["link"]] = c
        now = datetime.now(_TW).replace(tzinfo=None)
        cut = now - timedelta(hours=COLLECTION_WINDOW_HOURS)
        merged = []
        cal = datetime.now(_TW).strftime("%Y-%m-%d")
        for it in by_link.values():
            _hydrate_item_dt(it, cal)
            dt = it.get("_dt", datetime.min)
            if dt != datetime.min and dt >= cut:
                merged.append(it)
        merged.sort(key=lambda x: x.get("_dt", datetime.min), reverse=True)
        merged = merged[:300]
        self.save_recent_pool(merged)
        self._recent_pool_snapshot = merged
        logger.info(
            "recent_news_pool: 已保存 %d 則（%dh 內）",
            len(merged),
            COLLECTION_WINDOW_HOURS,
        )

    def _items_union_pool_today_news_for_llm(self) -> list:
        pool = self._recent_pool_snapshot
        if pool is None:
            pool = self.load_recent_pool()
        cal = datetime.now(_TW).strftime("%Y-%m-%d")

        def _prefer_newer(a: dict, b: dict) -> dict:
            _hydrate_item_dt(a, cal)
            _hydrate_item_dt(b, cal)
            da = a.get("_dt", datetime.min)
            db = b.get("_dt", datetime.min)
            return b if db > da else a

        by_link: dict = {}
        for it in self.today_news:
            if isinstance(it, dict) and it.get("link"):
                by_link[it["link"]] = it
        for it in pool:
            if not isinstance(it, dict) or not it.get("link"):
                continue
            link = it["link"]
            if link not in by_link:
                by_link[link] = it
            else:
                by_link[link] = _prefer_newer(by_link[link], it)
        out = list(by_link.values())
        for it in out:
            _hydrate_item_dt(it, cal)
        out.sort(key=lambda x: x.get("_dt", datetime.min), reverse=True)
        return out[:SUMMARY_ITEMS_LIMIT]

    def save_trend(self, avg_sentiment, count):
        trend_data: list = []
        if os.path.exists(self.trend_file):
            try:
                with open(self.trend_file, "r", encoding="utf-8") as f:
                    trend_data = json.load(f)
                if not isinstance(trend_data, list):
                    trend_data = []
            except Exception as e:
                logger.warning("無法讀取既有 sentiment_trends.json，將重建列表：%s", e)
                trend_data = []

        trend_data.append(
            {
                "timestamp": datetime.now(_TW).isoformat(),
                "average_sentiment": avg_sentiment,
                "news_count": count,
            }
        )
        if len(trend_data) > MAX_TREND_POINTS:
            trend_data = trend_data[-MAX_TREND_POINTS:]

        try:
            with open(self.trend_file, "w", encoding="utf-8") as f:
                json.dump(trend_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("寫入 sentiment_trends.json 失敗：%s", e)

    def _last_trend_value(self) -> Optional[float]:
        if not os.path.exists(self.trend_file):
            return None
        try:
            with open(self.trend_file, "r", encoding="utf-8") as f:
                trend_data = json.load(f)
            if isinstance(trend_data, list) and trend_data:
                return float(trend_data[-1]["average_sentiment"])
        except Exception:
            pass
        return None

    def is_new(self, url):
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
        if url_hash in self.history:
            return False
        self.history.append(url_hash)
        if len(self.history) > 1000:
            self.history = self.history[-1000:]
        return True

    def ensure_link_hashes(self, items):
        for item in items:
            link = item.get("link")
            if not link:
                continue
            url_hash = hashlib.md5(link.encode("utf-8")).hexdigest()
            if url_hash not in self.history:
                self.history.append(url_hash)
        if len(self.history) > 1000:
            self.history = self.history[-1000:]

    def filter_by_keywords(self, news_items, skip_dedup=False):
        filtered = []
        now = datetime.now(_TW).replace(tzinfo=None)
        cutoff_hours_ago = now - timedelta(hours=COLLECTION_WINDOW_HOURS)
        raw_n = len(news_items)
        stat_no_date = stat_old = stat_no_kw = stat_dup = 0

        last_pub_file = _abs_data("data/last_pub_time.txt")
        last_max_dt = cutoff_hours_ago
        if os.path.exists(last_pub_file):
            try:
                with open(last_pub_file, "r") as f:
                    last_max_dt = datetime.fromisoformat(f.read().strip())
            except Exception:
                pass

        current_max_dt = last_max_dt

        for item in news_items:
            published_str = item.get("published", "")
            item["_dt"] = datetime.min

            if published_str:
                try:
                    import dateutil.parser

                    pub_dt = dateutil.parser.parse(published_str)
                    if pub_dt.tzinfo:
                        pub_dt = pub_dt.astimezone().replace(tzinfo=None)
                    else:
                        pub_dt = pub_dt.replace(tzinfo=None)

                    item["_dt"] = pub_dt
                    item["display_time"] = pub_dt.strftime("%m/%d %H:%M")
                except Exception:
                    stat_no_date += 1
                    continue
            else:
                stat_no_date += 1
                continue

            if pub_dt < cutoff_hours_ago:
                stat_old += 1
                continue

            if pub_dt > current_max_dt:
                current_max_dt = pub_dt

            content = (item.get("title", "") + " " + item.get("summary", "")).lower()
            keyword_match = any(kw.lower() in content for kw in self.keywords)

            if not keyword_match:
                stat_no_kw += 1
                continue

            if skip_dedup:
                filtered.append(item)
            else:
                if not self.is_new(item["link"]):
                    stat_dup += 1
                    continue
                filtered.append(item)

        if skip_dedup:
            logger.info(
                "關鍵字篩選: 原始 %d 筆 → 保留 %d 筆（強制模式略過去重；無日期/解析失敗 %d，逾 %dh %d，無關鍵字 %d）",
                raw_n,
                len(filtered),
                stat_no_date,
                COLLECTION_WINDOW_HOURS,
                stat_old,
                stat_no_kw,
            )
        else:
            logger.info(
                "關鍵字篩選: 原始 %d 筆 → 保留 %d 筆（無日期/解析失敗 %d，逾 %dh %d，無關鍵字 %d，去重略過 %d）",
                raw_n,
                len(filtered),
                stat_no_date,
                COLLECTION_WINDOW_HOURS,
                stat_old,
                stat_no_kw,
                stat_dup,
            )

        if current_max_dt > last_max_dt:
            with open(last_pub_file, "w") as f:
                f.write(current_max_dt.isoformat())

        filtered.sort(key=lambda x: x.get("_dt", datetime.min), reverse=True)
        return filtered

    def summarize(self, items, force_refresh=False):
        existing_links = {news["link"] for news in self.today_news}
        new_unique_items = [item for item in items if item["link"] not in existing_links]

        has_new_content = len(new_unique_items) > 0

        if has_new_content:
            self.today_news.extend(new_unique_items)
            self.today_news.sort(key=lambda x: x.get("_dt", datetime.min), reverse=True)
            self.save_today_news()

        display_items = self._items_union_pool_today_news_for_llm()
        if not self.today_news and not display_items:
            return "目前沒有相關的新聞內容。"

        if force_refresh and not has_new_content:
            logger.info("強制更新：重新產生新聞清單（不使用 AI）。")

        lines = ["#### 今日財經要聞", ""]
        for item in display_items:
            time_info = f"[{item.get('display_time', '今日')}]"
            title = re.sub(r"\s+", " ", (item.get("title") or "").strip())
            source = (item.get("source") or "").strip()
            if source:
                lines.append(f"{time_info} {title}（{source}）")
            else:
                lines.append(f"{time_info} {title}")
            lines.append("")

        summary = "\n".join(lines).strip()
        summary = ensure_today_news_line_breaks(summary)
        summary = sort_today_news_section_newest_first(summary)

        fp = hashlib.md5(summary.encode("utf-8")).hexdigest()
        fp_path = _abs_data(LAST_SUMMARY_FP_FILE)
        last_fp = ""
        if os.path.exists(fp_path):
            try:
                with open(fp_path, "r", encoding="utf-8") as f:
                    last_fp = f.read().strip()
            except Exception:
                last_fp = ""

        if not has_new_content and not force_refresh and fp == last_fp:
            logger.info("本時段清單內容與上次相同，略過寫入 history 與走勢。")
            return None

        try:
            with open(fp_path, "w", encoding="utf-8") as f:
                f.write(fp)
        except Exception as e:
            logger.warning("寫入 last_summary_fp 失敗：%s", e)

        self._notify_channels = bool(has_new_content or force_refresh)
        if has_new_content or force_refresh:
            self.save_trend(50.0, len(self.today_news))

        if force_refresh and items:
            self.ensure_link_hashes(items)
        self.save_history()
        return summary
