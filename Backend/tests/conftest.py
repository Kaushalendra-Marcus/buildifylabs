import os
import sys
from pathlib import Path

# Make `app` importable when pytest runs from anywhere inside Backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Any module on the import chain (app.main, token_service, ...) constructs
# Settings() at import time, which requires the full env surface. Provide
# dummy values once, up front, so every test module can import app modules
# without a .env file present.
_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/dummy",
    "JWT_SECRET": "test-secret-not-for-prod",
    "FRONTEND_URL": "http://localhost:5173",
    "SMTP_HOST": "localhost",
    "SMTP_PORT": "25",
    "SMTP_USER": "u",
    "SMTP_PASS": "p",
    "EMAIL_FROM": "t@example.com",
    "CONTACT_FORM_RECIPIENT_EMAIL": "t@example.com",
    "GOOGLE_CLIENT_ID": "test-client",
}
for _key, _value in _REQUIRED_ENV.items():
    os.environ.setdefault(_key, _value)