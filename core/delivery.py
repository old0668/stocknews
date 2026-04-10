import logging
import os

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.line_token = os.getenv("LINE_NOTIFY_TOKEN", "").strip()

    async def notify_all(self, summary):
        text = summary if isinstance(summary, str) else str(summary)
        if len(text) > 8000:
            text = text[:7990] + "\n…"
        if self.telegram_token and self.telegram_chat:
            await self._telegram(text)
        if self.line_token:
            await self._line(text)

    async def _telegram(self, text):
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.telegram_chat, "text": text, "disable_web_page_preview": True}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, json=payload)
                if r.status_code >= 400:
                    logger.warning("Telegram 回應異常: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.error("Telegram 發送失敗: %s", e)

    async def _line(self, text):
        url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": f"Bearer {self.line_token}"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, headers=headers, data={"message": text[:2000]})
                if r.status_code >= 400:
                    logger.warning("LINE Notify 回應異常: %s %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.error("LINE Notify 失敗: %s", e)
