import aiosmtplib
from email.message import EmailMessage
from app.config import get_settings

settings = get_settings()

async def send_verification_email(to_email: str, token: str):
    link = f"{settings.FRONTEND_URL}/verify?token={token}"

    msg = EmailMessage()
    msg["Subject"] = "Verify your email"
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(f"Click to verify your email:\n{link}")

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASS,
        start_tls=True,
    )


async def send_password_reset_email(to_email: str, token: str):
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"

    msg = EmailMessage()
    msg["Subject"] = "Reset your password"
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(
        f"Click the link below to reset your password. This link expires in 20 minutes:\n{link}"
    )

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASS,
        start_tls=True,
    )