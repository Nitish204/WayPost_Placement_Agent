"""
Notification delivery: Telegram bot messages.

Email was removed entirely: Resend's free tier can only deliver to the
account owner's own address without a verified domain, and SMTP is
blocked outbound on Render's free tier - so no email channel could
reliably reach real users without either a paid host tier or a
purchased domain, neither of which fit this project's scope. Telegram
has no such restriction and was already working, so it's now the only
notification channel. Password recovery moved to an on-site security
question instead of an emailed reset link (see /auth/security-question
and /auth/reset-with-security-answer in main.py).
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def send_telegram(chat_id: str, text: str) -> bool:
    """Sends one message via the Telegram Bot API. Requires the user to
    have started a chat with the bot and linked their chat_id first
    (see /notifications/telegram/link)."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("[notifier] TELEGRAM_BOT_TOKEN not configured - skipping Telegram send")
        return False
    if not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("[notifier] telegram sent to chat_id=%s", chat_id)
        return True
    except Exception as e:
        logger.error("[notifier] telegram failed for chat_id=%s: %s", chat_id, e)
        return False


def format_match_telegram(jobs: list[dict]) -> str:
    lines = [f"<b>{len(jobs)} new job match{'es' if len(jobs) != 1 else ''}</b>"]
    for j in jobs:
        lines.append(
            f"\n<b>{j['title']}</b> at {j['company']}\n"
            f"{j['location']} — {j['score']}% match\n"
            f"<a href=\"{j['apply_url']}\">Apply here</a>"
        )
    return "\n".join(lines)
