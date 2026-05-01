import logging
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, request

from config import Config

from .database import close_db
from .errors import register_error_handlers
from .extensions import csrf, limiter, socketio
from .migrations import run_sql_migrations
from .routes import main_bp

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


def configure_logging(app):
    log_level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","message":"%(message)s"}',
    )


def _is_dev_mode(app):
    return app.config["DEBUG"] or app.config.get("APP_ENV") in {"development", "dev", "local"}


def _validate_security_config(app):
    is_dev = _is_dev_mode(app)
    secret_key = app.config.get("SECRET_KEY")
    known_weak_secrets = {
        None,
        "",
        "dev-secret-change-me",
        "change-this-in-production",
        "secret",
        "changeme",
    }
    if secret_key in known_weak_secrets:
        if is_dev:
            dev_secret_path = Path(app.config["DATABASE_PATH"]).resolve().with_name(
                ".dev_secret_key"
            )
            if dev_secret_path.exists():
                app.config["SECRET_KEY"] = dev_secret_path.read_text(
                    encoding="utf-8"
                ).strip()
            else:
                app.config["SECRET_KEY"] = f"dev-{secrets.token_hex(32)}"
                dev_secret_path.write_text(app.config["SECRET_KEY"], encoding="utf-8")
            app.logger.warning(
                "SECRET_KEY missing in dev mode; using stable local development key."
            )
        else:
            raise RuntimeError(
                "Refusing to start without a strong SECRET_KEY outside development."
            )

    if not is_dev and not app.config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError(
            "Refusing to start with SESSION_COOKIE_SECURE disabled outside development."
        )
    same_site = str(app.config.get("SESSION_COOKIE_SAMESITE", "Lax"))
    if same_site not in {"Lax", "Strict", "None"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be one of Lax, Strict, or None.")
    if same_site == "None" and not app.config.get("SESSION_COOKIE_SECURE"):
        raise RuntimeError(
            "SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE to be enabled."
        )

    cors_allowlist = app.config.get("SOCKETIO_CORS_ALLOWED_ORIGINS") or []
    if not cors_allowlist and is_dev:
        cors_allowlist = "*"
    if not cors_allowlist and not is_dev:
        raise RuntimeError(
            "SOCKETIO_CORS_ALLOWED_ORIGINS must be configured in non-development mode."
        )
    if not is_dev and "*" in cors_allowlist:
        raise RuntimeError("Wildcard Socket.IO CORS origin is not allowed in production.")
    app.config["SOCKETIO_CORS_ALLOWED_ORIGINS"] = cors_allowlist


def _register_security_headers(app):
    @app.after_request
    def apply_security_headers(response):
        if not app.config.get("SECURITY_HEADERS_ENABLED", True):
            return response

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "script-src 'self' https://cdn.socket.io; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "frame-ancestors 'none'; "
                "form-action 'self'"
            ),
        )

        if request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def create_app():
    if load_dotenv:
        load_dotenv()

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
        static_url_path="/static",
    )
    app.config.from_object(Config)
    _validate_security_config(app)
    app.permanent_session_lifetime = timedelta(
        seconds=app.config["PERMANENT_SESSION_LIFETIME_SECONDS"]
    )

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    configure_logging(app)

    csrf.init_app(app)
    limiter.init_app(app)
    socketio.init_app(
        app, cors_allowed_origins=app.config["SOCKETIO_CORS_ALLOWED_ORIGINS"]
    )
    app.register_blueprint(main_bp)
    app.teardown_appcontext(close_db)
    register_error_handlers(app)
    _register_security_headers(app)

    with app.app_context():
        run_sql_migrations(app)

    return app


__all__ = ["create_app", "socketio"]
