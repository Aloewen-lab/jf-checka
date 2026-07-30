"""E-Mail-Versand über SMTP.

Das Passwort wird bevorzugt aus einer Datei gelesen (`SMTP_PASS_FILE`), nicht aus
einer Umgebungsvariablen. Grund: so steht das Geheimnis an genau einer Stelle im
Dateisystem und nicht zusätzlich in `.env`, das man beim Debuggen ständig offen
hat. `SMTP_PASS` funktioniert als Fallback weiter.
"""

from __future__ import annotations

import os
import pathlib
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


class SmtpConfigError(RuntimeError):
    pass


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ.get("SMTP_USER", "").strip()
        if not user:
            raise SmtpConfigError("SMTP_USER fehlt in .env")

        password = os.environ.get("SMTP_PASS", "").strip()
        if not password:
            path = os.environ.get("SMTP_PASS_FILE", "").strip()
            if not path:
                raise SmtpConfigError(
                    "Weder SMTP_PASS noch SMTP_PASS_FILE gesetzt. "
                    "Gmail-App-Passwort unter https://myaccount.google.com/apppasswords "
                    "erzeugen (setzt 2FA voraus)."
                )
            file = pathlib.Path(path).expanduser()
            if not file.exists():
                raise SmtpConfigError(f"SMTP_PASS_FILE zeigt auf {file} — Datei fehlt.")
            # Gmail zeigt App-Passwörter in Vierergruppen an; die Leerzeichen
            # gehören nicht zum Passwort und werden hier entfernt.
            password = "".join(file.read_text().split())
            if not password:
                raise SmtpConfigError(f"{file} ist leer.")
        return cls(host=host, port=port, user=user, password=password)


def send(
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
    cfg: SmtpConfig | None = None,
) -> None:
    """Verschickt eine Mail. Wirft bei Fehlern — der Aufrufer entscheidet, ob ein
    fehlgeschlagener Versand den Collector-Lauf scheitern lässt (tut er nicht)."""
    cfg = cfg or SmtpConfig.from_env()

    msg = EmailMessage()
    msg["From"] = f"JF-Checka <{cfg.user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as server:
        server.starttls()
        server.login(cfg.user, cfg.password)
        server.send_message(msg)
