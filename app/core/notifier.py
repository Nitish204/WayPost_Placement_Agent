"""
Notification delivery: email + Telegram bot messages.

Email uses Resend's HTTP API (not raw SMTP) deliberately: many hosts,
including Render's free tier, block outbound traffic on SMTP ports
(25/465/587) entirely to prevent spam abuse - so smtplib-based sending
fails there with "Network is unreachable" regardless of how correct the
SMTP credentials are. Resend's API is a plain HTTPS POST on port 443,
which is never blocked, and its free tier (100 emails/day, 3000/month)
needs no credit card or domain to get started - use their shared
onboarding@resend.dev sender to send to your own verified address for
testing, or verify a real domain later to send to anyone.

Telegram is unaffected by this - it already used HTTPS.
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "onboarding@resend.dev")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Sends one HTML email via Resend's HTTPS API. Returns True/False
    instead of raising, so a bad send never crashes the caller
    (scheduler loop, forgot-password endpoint, etc)."""
    if not RESEND_API_KEY:
        logger.warning("[notifier] RESEND_API_KEY not configured - skipping email to %s", to_email)
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": RESEND_FROM, "to": [to_email], "subject": subject, "html": html_body},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("[notifier] email sent to %s", to_email)
        return True
    except requests.exceptions.HTTPError as e:
        # Resend returns a JSON body with the actual reason (e.g. "you can only
        # send to your own verified address on the shared onboarding domain")
        detail = e.response.text if e.response is not None else str(e)
        logger.error("[notifier] email failed for %s: %s", to_email, detail)
        return False
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


def format_reset_email(user_name: str, reset_url: str) -> tuple[str, str]:
    """Builds (subject, html_body) for a password reset email. The link
    contains the raw token as a query param - the frontend reads it from
    the URL and submits it to POST /auth/reset-password."""
    subject = "Reset your Waypost password"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;">
      <p>Hi {user_name},</p>
      <p>We got a request to reset your Waypost password. Click below to
      choose a new one - this link expires in 30 minutes.</p>
      <p style="margin:24px 0;">
        <a href="{reset_url}" style="background:#33513F;color:#fff;padding:12px 22px;
        border-radius:8px;text-decoration:none;font-weight:600;">Reset password</a>
      </p>
      <p style="color:#999;font-size:12px;">
        If you didn't request this, you can safely ignore this email - your
        password won't change unless you click the link above and set a new one.
      </p>
    </div>"""
    return subject, html
