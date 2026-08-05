"""
services/email.py — SMTP OTP email sender.
Only sends to @duk.ac.in addresses.
"""
import smtplib
import secrets
import logging
import email.utils
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from config import get_settings

logger = logging.getLogger(__name__)

# IST offset used for OTP timestamp display
_IST = timedelta(hours=5, minutes=30)


def generate_otp(length: int = 6) -> str:
    """Generate a zero-padded numeric OTP using a cryptographically secure source."""
    return str(secrets.randbelow(10**length)).zfill(length)


def send_otp_email(to_email: str, name: str, otp: str) -> bool:
    """
    Send an OTP verification email.
    Returns True on success, False on failure.
    """
    settings = get_settings()

    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("[EMAIL] SMTP_USER or SMTP_PASSWORD not set in .env. Skipping email dispatch.")
        # OTP is still logged below for development testing
        logger.info("[OTP] (Dev fallback) Code for %s: %s", to_email, otp)
        return False

    ist_now = (datetime.now(timezone.utc) + _IST).strftime("%Y-%m-%d %H:%M:%S")

    html_body = f"""<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; font-family: Arial, sans-serif; background:#ffffff; color:#222222;">
  <div style="max-width:560px; margin:32px auto; padding:0 16px;">

    <p style="margin:0 0 16px;">Dear <strong>{name}</strong>,</p>

    <p style="margin:0 0 16px; line-height:1.6;">
      You are attempting to sign in to your <strong>DUK Bus Tracker</strong> account
      using the registered email address <strong>{to_email}</strong>.
    </p>

    <p style="margin:0 0 16px; line-height:1.6;">
      Your <span style="background:#fff3cd; padding:1px 4px; border-radius:3px;">
      <strong>One-Time Password</strong> (OTP)</span>
      for verifying your DUK Bus Tracker account,
      generated at <strong>{ist_now} IST</strong>, is:
    </p>

    <!-- OTP Box -->
    <div style="background:#f4f4f4; border:1px solid #dddddd; border-radius:4px;
                padding:18px 24px; margin:0 0 20px; display:inline-block; min-width:200px;">
      <span style="font-family:'Courier New', Courier, monospace;
                   font-size:34px; font-weight:700; letter-spacing:8px; color:#111111;">
        {otp}
      </span>
    </div>

    <p style="margin:0 0 8px; line-height:1.6;">
      This <span style="background:#fff3cd; padding:1px 4px; border-radius:3px;"><strong>OTP</strong></span>
      is valid for <strong>10 minutes</strong> and not to be shared with anyone.
    </p>

    <p style="margin:0 0 24px; line-height:1.6;">
      If you did not initiate this request, please ignore this email.
    </p>

    <p style="margin:0 0 4px;">Regards,</p>
    <p style="margin:0 0 32px;"><strong>DUK Bus Tracker Team</strong><br>
      <span style="color:#888; font-size:13px;">Digital University Kerala</span>
    </p>

    <hr style="border:none; border-top:1px solid #eeeeee; margin:0 0 16px;">

    <p style="margin:0; font-size:12px; color:#888888; font-style:italic;">
      This is an auto-generated email. Do not reply to this email.
    </p>

  </div>
</body>
</html>"""

    # Format From header cleanly (e.g. "DUK Bus Tracker <email@duk.ac.in>")
    from_header = settings.SMTP_FROM or settings.SMTP_USER
    # Pure email address for SMTP envelope MAIL FROM
    envelope_sender = email.utils.parseaddr(from_header)[1] or settings.SMTP_USER

    msg = MIMEText(html_body, "html")
    msg["Subject"] = f"Your DUK Bus Tracker verification code: {otp}"
    msg["From"]    = from_header
    msg["To"]      = to_email

    # Always log OTP so dev can verify even if email delivery fails
    logger.info("[OTP] Code for %s: %s", to_email, otp)

    try:
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(envelope_sender, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(envelope_sender, [to_email], msg.as_string())
        logger.info("[EMAIL] OTP sent to %s", to_email)
        return True
    except Exception as exc:
        logger.exception("[EMAIL] Failed to send OTP to %s: %s", to_email, exc)
        return False

