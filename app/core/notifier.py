"""
Notification delivery: email (SMTP) + Telegram bot messages.

This is the module the README flagged as missing - `MatchResult.notified`
existed in the schema but nothing actually sent anything. Wired up here
and called from the scheduler after each ingestion cycle (see
app/scheduler.py: notify_new_matches).

Both channels are best-effort and independently fault-tolerant: a
failure in one never blocks the other, and a failure here never blocks
ingestion itself.
"""
import os
import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Sends one HTML email via SMTP. Returns True/False instead of
    raising, so a bad send never crashes the caller (scheduler loop)."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        logger.warning("[notifier] SMTP not configured - skipping email to %s", to_email)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        logger.info("[notifier] email sent to %s", to_email)
        return True
    except Exception as e:
        logger.error("[notifier] email failed for %s: %s", to_email, e)
        return False


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


def format_match_email(user_name: str, jobs: list[dict]) -> tuple[str, str]:
    """Builds (subject, html_body) for a batch of new job matches."""
    subject = f"{len(jobs)} new job match{'es' if len(jobs) != 1 else ''} for you"
    rows = ""
    for j in jobs:
        rows += f"""
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid #eee;">
            <div style="font-weight:600;font-size:15px;">{j['title']} · {j['company']}</div>
            <div style="color:#666;font-size:13px;">{j['location']} — match score {j['score']}%</div>
            <a href="{j['apply_url']}" style="font-size:13px;color:#1a73e8;">View and apply</a>
          </td>
        </tr>"""
    html = f"""
    <div style="font-family:sans-serif;max-width:520px;">
      <p>Hi {user_name},</p>
      <p>We found {len(jobs)} new job{'s' if len(jobs) != 1 else ''} that match your profile:</p>
      <table style="width:100%;border-collapse:collapse;">{rows}</table>
      <p style="color:#999;font-size:12px;margin-top:20px;">
        You're receiving this because you have job alerts enabled. Manage this in your profile settings.
      </p>
    </div>"""
    return subject, html


def format_match_telegram(jobs: list[dict]) -> str:
    lines = [f"<b>{len(jobs)} new job match{'es' if len(jobs) != 1 else ''}</b>"]
    for j in jobs:
        lines.append(
            f"\n<b>{j['title']}</b> at {j['company']}\n"
            f"{j['location']} — {j['score']}% match\n"
            f"<a href=\"{j['apply_url']}\">Apply here</a>"
        )
    return "\n".join(lines)
