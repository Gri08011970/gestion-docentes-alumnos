# notify.py
import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.utils import formataddr
from email import encoders

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
ALERTS_TO = [e.strip() for e in os.getenv("ALERTS_TO","").split(",") if e.strip()]

def send_email(subject: str, html: str, to_list=None, attachment=None):
    """
    attachment: tuple (filename, bytes_data, mime_type) o None
    """
    if not to_list: to_list = ALERTS_TO
    if not to_list or not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        return False, "SMTP not configured"

    name, addr = (SMTP_FROM.split("<")[0].strip(), SMTP_FROM.split("<")[-1].rstrip(">").strip()) if "<" in SMTP_FROM else (SMTP_FROM, SMTP_FROM)

    if attachment:
        msg = MIMEMultipart()
        msg.attach(MIMEText(html, "html", "utf-8"))
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment[1])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{attachment[0]}"')
        # si llega mime_type, podés ignorarlo o crear MIMEApplication
        msg.attach(part)
    else:
        msg = MIMEText(html, "html", "utf-8")

    msg["From"] = formataddr((name, addr))
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(addr, to_list, msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, str(e)
