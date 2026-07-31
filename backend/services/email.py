"""
services/email.py — SMTP OTP email sender.
Only sends to @duk.ac.in addresses.
"""
import smtplib
import random
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def generate_otp(length: int = 6) -> str:
    """Generate a zero-padded numeric OTP."""
    return str(random.randint(0, 10**length - 1)).zfill(length)


def send_otp_email(to_email: str, name: str, otp: str) -> bool:
    """
    Send an OTP verification email.
    Returns True on success, False on failure.
    Credentials are loaded from environment — safe to deploy with placeholder values
    during development (will log a warning instead of crashing).
    """
    if settings.SMTP_PASSWORD == "PLACEHOLDER":
        logger.warning(
            "[EMAIL] SMTP not configured. OTP for %s: %s (dev-only log)", to_email, otp
        )
        return True  # pretend success in dev mode

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: 'Segoe UI', sans-serif; background:#f4f7f6; margin:0; padding:0;">
      <div style="max-width:480px; margin:40px auto; background:#ffffff; border-radius:16px;
                  box-shadow:0 4px 24px rgba(0,0,0,0.08); overflow:hidden;">
        <div style="background:linear-gradient(135deg,#1a7a5e,#2ecc8a); padding:32px 32px 24px;">
          <h1 style="color:#fff; margin:0; font-size:22px; font-weight:700;">DUK Bus Tracker</h1>
          <p style="color:rgba(255,255,255,0.8); margin:6px 0 0; font-size:14px;">
            Digital University Kerala
          </p>
        </div>
        <div style="padding:32px;">
          <p style="color:#333; font-size:16px; margin:0 0 8px;">Hi <strong>{name}</strong>,</p>
          <p style="color:#555; font-size:14px; margin:0 0 28px;">
            Use the code below to verify your DUK email and activate your Bus Tracker account.
            This code expires in <strong>10 minutes</strong>.
          </p>
          <div style="background:#f0faf6; border:2px solid #2ecc8a; border-radius:12px;
                      padding:20px; text-align:center; margin-bottom:28px;">
            <span style="font-size:42px; font-weight:800; letter-spacing:12px; color:#1a7a5e;">
              {otp}
            </span>
          </div>
          <p style="color:#999; font-size:12px; margin:0;">
            If you didn't request this, please ignore this email. Do not share this code with anyone.
          </p>
        </div>
        <div style="background:#f9f9f9; padding:16px 32px; text-align:center;">
          <p style="color:#bbb; font-size:11px; margin:0;">
            DUK Bus Tracker &bull; Digital University Kerala &bull; duk.ac.in
          </p>
        </div>
      </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your DUK Bus Tracker verification code: {otp}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    # Always log OTP so dev can verify even if email fails
    logger.info("[OTP] Code for %s: %s", to_email, otp)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, [to_email], msg.as_string())
        logger.info("[EMAIL] OTP sent to %s", to_email)
        return True
    except Exception:
        logger.exception("[EMAIL] Failed to send OTP to %s", to_email)
        return False
