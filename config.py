import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name, default=""):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    DEBUG = _env_bool("FLASK_DEBUG", APP_ENV in {"development", "dev", "local"})

    SECRET_KEY = os.getenv("SECRET_KEY")
    DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "database.db"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", not DEBUG)

    PERMANENT_SESSION_LIFETIME_SECONDS = int(
        os.getenv("PERMANENT_SESSION_LIFETIME_SECONDS", "86400")
    )
    MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "500"))
    MIN_SECONDS_BETWEEN_MESSAGES = float(os.getenv("MIN_SECONDS_BETWEEN_MESSAGES", "1"))
    EDIT_WINDOW_SECONDS = int(os.getenv("EDIT_WINDOW_SECONDS", "600"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(5 * 1024 * 1024)))
    ALLOWED_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))

    DEFAULT_RATE_LIMIT = os.getenv("DEFAULT_RATE_LIMIT", "200 per hour")
    AUTH_RATE_LIMIT = os.getenv("AUTH_RATE_LIMIT", "10 per minute")
    MESSAGE_RATE_LIMIT = os.getenv("MESSAGE_RATE_LIMIT", "30 per minute")
    SENSITIVE_RATE_LIMIT = os.getenv("SENSITIVE_RATE_LIMIT", "20 per minute")
    PASSWORD_RESET_RATE_LIMIT = os.getenv("PASSWORD_RESET_RATE_LIMIT", "3 per 15 minutes")

    SOCKETIO_CORS_ALLOWED_ORIGINS = _env_list("SOCKETIO_CORS_ALLOWED_ORIGINS", "")
    SECURITY_HEADERS_ENABLED = _env_bool("SECURITY_HEADERS_ENABLED", True)
