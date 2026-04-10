#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import platform
import re
import yaml
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from core.ingestion import Ingestor
from core.processing import Processor
from core.delivery import Notifier

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Must match processor summary header (#### today finance headlines).
TODAY_HEADLINES_HEADER = "#### \u4eca\u65e5\u8ca1\u7d93\u8981\u805e"


async def _close_gemini_client(client):
    if client is None:
        return
    try:
        for name in ("aclose", "close"):
            fn = getattr(client, name, None)
            if callable(fn):
                ret = fn()
                if asyncio.iscoroutine(ret):
                    await ret
                return
        api = getattr(client, "_api_client", None)
        if api is not None:
            fn = getattr(api, "aclose", None)
            if callable(fn):
                ret = fn()
                if asyncio.iscoroutine(ret):
                    await ret
    except Exception as e:
        logger.debug("Gemini client cleanup: %s", e)


def _peak_time_in_today_section(summary_md: str, ref_year: int) -> Optional[datetime]:
    """Latest [MM/DD HH:MM] under today headlines block; None if missing."""
    if not summary_md or TODAY_HEADLINES_HEADER not in summary_md:
        return None
    si = summary_md.index(TODAY_HEADLINES_HEADER) + len(TODAY_HEADLINES_HEADER)
    ei = len(summary_md)
    for em in ("\n#### ", "\n---"):
        p = summary_md.find(em, si)
        if p != -1:
            ei = min(ei, p)
    body = summary_md[si:ei]
    peaks: list[datetime] = []
    for m in re.finditer(r"\[(\d{2})/(\d{2})\s+(\d{2}):(\d{2})\]", body):
        mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            peaks.append(datetime(ref_year, mo, d, h, mi))
        except ValueError:
            continue
    return max(peaks) if peaks else None


def _extract_markdown_from_history_entry(entry: str) -> str:
    """Markdown body of a history entry (without update-time div)."""
    if not entry or not isinstance(entry, str):
        return ""
    marker = "</div>\n\n"
    i = entry.find(marker)
    if i == -1:
        t = entry.strip()
        return t if t.startswith("####") else ""
    start = i + len(marker)
    end = entry.rfind("\n\n---")
    if end == -1 or end <= start:
        return entry[start:].strip()
    return entry[start:end].strip()


def load_config(config_path="config/config.yaml"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_config_path = os.path.join(base_dir, config_path)
    with open(full_config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_aggregator(force_refresh: bool = False):
    """
    force_refresh=True: skip URL dedup, merge candidates + today news, force LLM path.
    """
    processor = None
    try:
        if platform.system() in ("Darwin", "Windows"):
            logger.info("Detected %s, initializing...", platform.system())

        logger.info("Starting News Aggregator Core Logic...")
        if force_refresh:
            logger.info("Force refresh enabled (skip dedup, regenerate summary).")

        config = load_config()

        ingestor = Ingestor(config["news_sources"])
        raw_news = ingestor.fetch_all()
        logger.info("Fetched %d items from sources.", len(raw_news))

        processor = Processor(config["keywords"], config["llm"])
        candidates = processor.filter_by_keywords(raw_news, skip_dedup=True)
        processor.merge_recent_pool_from_candidates(candidates)
        if force_refresh:
            filtered_news = candidates
        else:
            filtered_news = processor.filter_by_keywords(raw_news, skip_dedup=False)
        logger.info(
            "Found %d items after filtering%s.",
            len(filtered_news),
            " (skip dedup)" if force_refresh else " and deduplication",
        )

        logger.info("Generating LLM summary (or recording flat trend)...")
        summary = processor.summarize(filtered_news, force_refresh=force_refresh)

        if summary is None:
            logger.info("No new news; LLM and trend file unchanged.")
            return None, filtered_news

        if getattr(processor, "_notify_channels", True):
            notifier = Notifier()
            logger.info("Delivering summary to configured channels...")
            await notifier.notify_all(summary)
        else:
            logger.info("Skip Telegram/LINE: web list refresh only (no brand-new links).")

        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            history_path = os.path.join(base_dir, "data", "history.json")
            tw = ZoneInfo("Asia/Taipei")
            timestamp = datetime.now(tw).strftime("%Y-%m-%d %H:%M")
            web_content = (
                f"<div class='update-time'>\U0001f552 \u66f4\u65b0\u6642\u9593\uff1a{timestamp}</div>\n\n"
                f"{summary}\n\n---"
            )

            summaries = []
            if os.path.exists(history_path):
                with open(history_path, "r", encoding="utf-8") as f:
                    try:
                        summaries = json.load(f)
                    except Exception:
                        summaries = []

            new_md = summary.strip()
            ref_year = datetime.now(tw).year
            new_peak = _peak_time_in_today_section(new_md, ref_year)
            history_changed = False

            if summaries:
                old_md = _extract_markdown_from_history_entry(summaries[0])
                if old_md == new_md:
                    summaries[0] = web_content
                    history_changed = True
                    logger.info("Same headline list as first entry; refresh update time only.")
                else:
                    old_peak = _peak_time_in_today_section(old_md, ref_year)
                    if new_peak and old_peak and new_peak < old_peak:
                        logger.warning(
                            "New run peak story time %s is older than current history %s "
                            "(RSS snapshot differs by runner/region); skip history prepend.",
                            new_peak.strftime("%m/%d %H:%M"),
                            old_peak.strftime("%m/%d %H:%M"),
                        )
                    else:
                        summaries.insert(0, web_content)
                        history_changed = True
            else:
                summaries.insert(0, web_content)
                history_changed = True

            if history_changed:
                summaries = summaries[:50]
                with open(history_path, "w", encoding="utf-8") as f:
                    json.dump(summaries, f, ensure_ascii=False, indent=2)
                logger.info("Successfully updated %s for web display.", history_path)
            elif summaries:
                logger.info("history.json unchanged (downgrade skipped).")
        except Exception as e:
            logger.error("Error updating web history: %s", e)

        return summary, filtered_news
    finally:
        if processor is not None:
            await _close_gemini_client(getattr(processor, "gemini_client", None))


async def main():
    summary, _ = await run_aggregator()
    if summary:
        print("\n--- Generated Summary ---\n")
        print(summary)
        print("\n-------------------------\n")

    if platform.system() == "Darwin":
        print("\u2705 Task done (macOS).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
