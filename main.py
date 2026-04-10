#!/usr/bin/env python3
import asyncio
import yaml
import os
import logging
import platform
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


def load_config(config_path="config/config.yaml"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_config_path = os.path.join(base_dir, config_path)
    with open(full_config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_aggregator(force_refresh: bool = False):
    """
    force_refresh=True：略過 URL 去重，以 48h+關鍵字候選合併今日新聞並強制呼叫 LLM。
    """
    processor = None
    try:
        if platform.system() in ("Darwin", "Windows"):
            logger.info("檢測到 %s 環境，正在初始化...", platform.system())

        logger.info("Starting News Aggregator Core Logic...")
        if force_refresh:
            logger.info("強制更新模式已啟用（略過去重，將重新產生摘要）。")

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
            logger.info("略過 Telegram/LINE：僅同步網頁清單（時間排序或 pool 更新，無全新連結）。")

        try:
            import json
            from datetime import datetime
            from zoneinfo import ZoneInfo

            base_dir = os.path.dirname(os.path.abspath(__file__))
            history_path = os.path.join(base_dir, "data", "history.json")
            timestamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M")
            web_content = f"<div class='update-time'>🕒 更新時間：{timestamp}</div>\n\n{summary}\n\n---"

            summaries = []
            if os.path.exists(history_path):
                with open(history_path, "r", encoding="utf-8") as f:
                    try:
                        summaries = json.load(f)
                    except Exception:
                        summaries = []

            summaries.insert(0, web_content)
            summaries = summaries[:50]

            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(summaries, f, ensure_ascii=False, indent=2)
            logger.info("Successfully updated %s for web display.", history_path)
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
        print("✅ 任務完成！已發送系統通知。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n使用者中斷執行。")
