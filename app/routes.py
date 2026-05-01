import json
import secrets
import time
from hashlib import sha256
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_socketio import emit, join_room
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from .database import get_db
from .extensions import limiter, socketio
from .forms import (
    EditMessageForm,
    LoginForm,
    MessageForm,
    PasswordResetForm,
    PasswordResetRequestForm,
    ProfileForm,
    SearchForm,
    SignupForm,
)
from .validators import (
    validate_message,
    validate_password,
    validate_upload,
    validate_username,
)


main_bp = Blueprint("main", __name__)


def is_logged_in():
    return "user_id" in session


def _token_hash(token):
    secret = current_app.config["SECRET_KEY"]
    return sha256(f"{secret}:{token}".encode("utf-8")).hexdigest()


def log_audit(action, target_type, target_id=None, details=None):
    db = get_db()
    db.execute(
        """
        INSERT INTO audit_logs (actor_id, action, target_type, target_id, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session.get("user_id"),
            action,
            target_type,
            target_id,
            json.dumps(details or {}),
        ),
    )
    db.commit()


def _touch_presence(is_online=True):
    if not is_logged_in():
        return
    db = get_db()
    db.execute(
        "UPDATE users SET is_online = ?, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
        (1 if is_online else 0, session["user_id"]),
    )
    db.commit()


def _emit_status_update(target_user_id, message_ids, status):
    if not message_ids:
        return
    socketio.emit(
        "message_status",
        {
            "message_ids": message_ids,
            "status": status,
        },
        to=f"user_{target_user_id}",
    )


def _conversation_room(user_id, other_user_id):
    first_id, second_id = sorted((int(user_id), int(other_user_id)))
    return f"conversation_{first_id}_{second_id}"


def _mark_delivered(sender_id, notify=True):
    db = get_db()
    rows = db.execute(
        """
        SELECT id
        FROM messages
        WHERE sender_id = ? AND receiver_id = ? AND status = 'sent' AND is_deleted = 0
        """,
        (sender_id, session["user_id"]),
    ).fetchall()
    message_ids = [row["id"] for row in rows]
    if not message_ids:
        return []
    db.execute(
        f"""
        UPDATE messages
        SET status = 'delivered', delivered_at = CURRENT_TIMESTAMP
        WHERE id IN ({",".join("?" for _ in message_ids)})
        """,
        message_ids,
    )
    db.commit()
    if notify:
        _emit_status_update(sender_id, message_ids, "delivered")
    return message_ids


def _mark_seen(chat_user_id, notify=True):
    db = get_db()
    rows = db.execute(
        """
        SELECT id
        FROM messages
        WHERE sender_id = ? AND receiver_id = ? AND status IN ('sent', 'delivered') AND is_deleted = 0
        """,
        (chat_user_id, session["user_id"]),
    ).fetchall()
    message_ids = [row["id"] for row in rows]
    if not message_ids:
        return []
    db.execute(
        f"""
        UPDATE messages
        SET status = 'seen', read_at = CURRENT_TIMESTAMP
        WHERE id IN ({",".join("?" for _ in message_ids)})
        """,
        message_ids,
    )
    db.commit()
    if notify:
        _emit_status_update(chat_user_id, message_ids, "seen")
    return message_ids


def _create_message(sender_id, receiver_id, message, client_nonce=None):
    db = get_db()
    target = db.execute("SELECT id FROM users WHERE id = ?", (receiver_id,)).fetchone()
    if not target:
        return None

    cursor = db.execute(
        """
        INSERT INTO messages (
            sender_id,
            receiver_id,
            message,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 'sent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (sender_id, receiver_id, message),
    )
    db.commit()

    payload = {
        "id": cursor.lastrowid,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "message": message,
        "status": "sent",
    }
    if client_nonce:
        payload["client_nonce"] = client_nonce
    return payload


def _broadcast_message(payload):
    socketio.emit("new_message", payload, to=f"user_{payload['sender_id']}")
    socketio.emit("new_message", payload, to=f"user_{payload['receiver_id']}")
    socketio.emit(
        "new_message",
        payload,
        to=_conversation_room(payload["sender_id"], payload["receiver_id"]),
    )


def _mark_single_message_status(message_id, status):
    db = get_db()
    message = db.execute(
        """
        SELECT id, sender_id, receiver_id, status
        FROM messages
        WHERE id = ? AND receiver_id = ? AND is_deleted = 0
        """,
        (message_id, session["user_id"]),
    ).fetchone()
    if not message:
        return None

    if status == "delivered":
        if message["status"] != "sent":
            return message["sender_id"]
        db.execute(
            """
            UPDATE messages
            SET status = 'delivered', delivered_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (message_id,),
        )
    elif status == "seen":
        if message["status"] == "seen":
            return message["sender_id"]
        db.execute(
            """
            UPDATE messages
            SET status = 'seen', delivered_at = COALESCE(delivered_at, CURRENT_TIMESTAMP), read_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (message_id,),
        )
    else:
        return None

    db.commit()
    _emit_status_update(message["sender_id"], [message_id], status)
    return message["sender_id"]


def _serialize_message(row):
    return {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "receiver_id": row["receiver_id"],
        "message": row["message"],
        "status": row["status"],
        "is_deleted": row["is_deleted"],
        "created_at": row["created_at"],
    }


def _load_conversation_messages(current_user_id, other_user_id, after_id=None):
    db = get_db()
    query = """
        SELECT id, sender_id, receiver_id, message, status, is_deleted, created_at
        FROM messages
        WHERE (
            (sender_id = ? AND receiver_id = ?)
            OR
            (sender_id = ? AND receiver_id = ?)
        )
    """
    params = [current_user_id, other_user_id, other_user_id, current_user_id]
    if after_id is not None:
        query += " AND id > ?"
        params.append(after_id)
    query += " ORDER BY created_at, id"
    rows = db.execute(query, params).fetchall()
    return [_serialize_message(row) for row in rows]


@main_bp.route("/")
def home():
    return render_template("index.html")


@main_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["AUTH_RATE_LIMIT"])
def login():
    if is_logged_in():
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        username_ok, username = validate_username(form.username.data)
        if not username_ok:
            flash(username, "error")
            return redirect(url_for("main.login"))

        db = get_db()
        user = db.execute(
            "SELECT id, username, password FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if user and check_password_hash(user["password"], form.password.data):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session.permanent = True
            _touch_presence(True)
            return redirect(url_for("main.dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", form=form)


@main_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["AUTH_RATE_LIMIT"])
def signup():
    if is_logged_in():
        return redirect(url_for("main.dashboard"))

    form = SignupForm()
    if form.validate_on_submit():
        username_ok, username = validate_username(form.username.data)
        if not username_ok:
            flash(username, "error")
            return redirect(url_for("main.signup"))

        password_ok, password = validate_password(form.password.data)
        if not password_ok:
            flash(password, "error")
            return redirect(url_for("main.signup"))

        hashed_password = generate_password_hash(password)
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username, password, display_name) VALUES (?, ?, ?)",
                (username, hashed_password, username),
            )
            db.commit()
            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("main.login"))
        except Exception:
            flash("Username already exists.", "error")
    return render_template("signup.html", form=form)


@main_bp.route("/dashboard")
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def dashboard():
    if not is_logged_in():
        return redirect(url_for("main.login"))
    _touch_presence(True)
    db = get_db()

    q = request.args.get("q", "").strip()
    query = """
        SELECT
            u.id,
            COALESCE(u.display_name, u.username) AS name,
            u.username,
            u.is_online,
            (
                SELECT m.message FROM messages m
                WHERE ((m.sender_id = u.id AND m.receiver_id = ?) OR (m.sender_id = ? AND m.receiver_id = u.id))
                  AND m.is_deleted = 0
                ORDER BY m.created_at DESC
                LIMIT 1
            ) AS last_message,
            (
                SELECT COUNT(*) FROM messages um
                WHERE um.sender_id = u.id AND um.receiver_id = ? AND um.read_at IS NULL AND um.is_deleted = 0
            ) AS unread_count
        FROM users u
        WHERE u.id != ?
    """
    params = [session["user_id"], session["user_id"], session["user_id"], session["user_id"]]
    if q:
        query += " AND (u.username LIKE ? OR COALESCE(u.display_name, u.username) LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like])
    query += " ORDER BY unread_count DESC, name ASC"
    users = db.execute(query, params).fetchall()

    selected_user = request.args.get("chat", type=int)
    selected_user_row = None
    messages = []
    message_form = MessageForm()
    edit_form = EditMessageForm()
    if selected_user:
        selected_user_row = db.execute(
            "SELECT id, username, COALESCE(display_name, username) AS display_name FROM users WHERE id = ?",
            (selected_user,),
        ).fetchone()
        if selected_user_row:
            _mark_delivered(selected_user_row["id"])
            _mark_seen(selected_user_row["id"])
            messages = _load_conversation_messages(session["user_id"], selected_user)
        else:
            selected_user = None

    search_form = SearchForm()
    return render_template(
        "dashboard.html",
        users=users,
        selected_user=selected_user,
        selected_username=selected_user_row["display_name"] if selected_user_row else None,
        messages=messages,
        form=message_form,
        edit_form=edit_form,
        search_form=search_form,
    )


@main_bp.route("/chat/<int:user_id>", methods=["POST"])
@limiter.limit(lambda: current_app.config["MESSAGE_RATE_LIMIT"])
def chat(user_id):
    if not is_logged_in():
        return redirect(url_for("main.login"))

    now = time.time()
    last_message_time = session.get("last_msg_time")
    if last_message_time and (now - last_message_time) < current_app.config["MIN_SECONDS_BETWEEN_MESSAGES"]:
        flash("You are sending messages too quickly.", "error")
        return redirect(url_for("main.dashboard", chat=user_id))

    form = MessageForm()
    if not form.validate_on_submit():
        flash("Invalid message payload.", "error")
        return redirect(url_for("main.dashboard", chat=user_id))

    message_ok, message_or_error = validate_message(
        form.message.data, current_app.config["MAX_MESSAGE_LENGTH"]
    )
    if not message_ok:
        flash(message_or_error, "error")
        return redirect(url_for("main.dashboard", chat=user_id))

    client_nonce = (request.form.get("client_nonce") or "").strip()[:64]
    payload = _create_message(
        session["user_id"], user_id, message_or_error, client_nonce=client_nonce or None
    )
    if not payload:
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": "User not found."}), 404
        flash("User not found.", "error")
        return redirect(url_for("main.dashboard"))

    log_audit("message.sent", "message", payload["id"], {"receiver_id": user_id})
    session["last_msg_time"] = now
    _broadcast_message(payload)
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": True, "message": payload})
    return redirect(url_for("main.dashboard", chat=user_id))


@main_bp.route("/api/chat/<int:user_id>/messages")
@limiter.limit("300 per minute")
def chat_messages_api(user_id):
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Authentication required."}), 401

    db = get_db()
    target = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "User not found."}), 404

    after_id = request.args.get("after_id", type=int)
    _mark_delivered(user_id)
    _mark_seen(user_id)
    messages = _load_conversation_messages(session["user_id"], user_id, after_id=after_id)
    response = jsonify({"ok": True, "messages": messages})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@main_bp.route("/messages/<int:message_id>/edit", methods=["POST"])
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def edit_message(message_id):
    if not is_logged_in():
        return redirect(url_for("main.login"))
    form = EditMessageForm()
    chat_id = request.form.get("chat", type=int)
    if not form.validate_on_submit():
        flash("Invalid edit payload.", "error")
        return redirect(url_for("main.dashboard", chat=chat_id))

    db = get_db()
    message = db.execute(
        "SELECT * FROM messages WHERE id = ? AND sender_id = ?",
        (message_id, session["user_id"]),
    ).fetchone()
    if not message:
        flash("Message not found.", "error")
        return redirect(url_for("main.dashboard", chat=chat_id))

    created_at = datetime.fromisoformat(message["created_at"])
    if datetime.now() - created_at > timedelta(seconds=current_app.config["EDIT_WINDOW_SECONDS"]):
        flash("Edit window expired.", "error")
        return redirect(url_for("main.dashboard", chat=chat_id))

    message_ok, message_or_error = validate_message(
        form.message.data, current_app.config["MAX_MESSAGE_LENGTH"]
    )
    if not message_ok:
        flash(message_or_error, "error")
        return redirect(url_for("main.dashboard", chat=chat_id))

    db.execute(
        "UPDATE messages SET message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (message_or_error, message_id),
    )
    db.commit()
    log_audit("message.edited", "message", message_id, {"chat_id": chat_id})
    flash("Message edited.", "success")
    return redirect(url_for("main.dashboard", chat=chat_id))


@main_bp.route("/messages/<int:message_id>/delete", methods=["POST"])
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def delete_message(message_id):
    if not is_logged_in():
        return redirect(url_for("main.login"))
    chat_id = request.form.get("chat", type=int)
    db = get_db()
    message = db.execute(
        "SELECT * FROM messages WHERE id = ? AND sender_id = ?",
        (message_id, session["user_id"]),
    ).fetchone()
    if not message:
        flash("Message not found.", "error")
        return redirect(url_for("main.dashboard", chat=chat_id))

    created_at = datetime.fromisoformat(message["created_at"])
    if datetime.now() - created_at > timedelta(seconds=current_app.config["EDIT_WINDOW_SECONDS"]):
        flash("Delete window expired.", "error")
        return redirect(url_for("main.dashboard", chat=chat_id))

    db.execute(
        """
        UPDATE messages
        SET is_deleted = 1, message = '[deleted]', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (message_id,),
    )
    db.commit()
    log_audit("message.deleted", "message", message_id, {"chat_id": chat_id})
    flash("Message deleted.", "success")
    return redirect(url_for("main.dashboard", chat=chat_id))


@main_bp.route("/search")
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def search_messages():
    if not is_logged_in():
        return redirect(url_for("main.login"))
    chat_id = request.args.get("chat", type=int)
    q = request.args.get("q", "").strip()
    results = []
    if chat_id and q:
        db = get_db()
        results = db.execute(
            """
            SELECT id, sender_id, message, created_at
            FROM messages
            WHERE (
                (sender_id = ? AND receiver_id = ?)
                OR
                (sender_id = ? AND receiver_id = ?)
            )
            AND message LIKE ?
            AND is_deleted = 0
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (session["user_id"], chat_id, chat_id, session["user_id"], f"%{q}%"),
        ).fetchall()
    return render_template("search_results.html", results=results, chat_id=chat_id, q=q)


@main_bp.route("/profile", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def profile():
    if not is_logged_in():
        return redirect(url_for("main.login"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    form = ProfileForm()
    if request.method == "GET":
        form.display_name.data = user["display_name"] or ""
        form.bio.data = user["bio"] or ""

    if form.validate_on_submit():
        avatar_path = user["avatar_path"]
        if form.avatar.data:
            upload_ok, upload_result = validate_upload(
                form.avatar.data,
                current_app.config["ALLOWED_UPLOAD_EXTENSIONS"],
            )
            if not upload_ok:
                flash(upload_result, "error")
                return redirect(url_for("main.profile"))

            filename = f"user_{session['user_id']}_{secrets.token_hex(8)}{upload_result}"
            upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "avatars"
            upload_dir.mkdir(parents=True, exist_ok=True)
            save_path = upload_dir / secure_filename(filename)
            form.avatar.data.save(save_path)
            avatar_path = f"avatars/{save_path.name}"

        db.execute(
            "UPDATE users SET display_name = ?, bio = ?, avatar_path = ? WHERE id = ?",
            (form.display_name.data.strip(), form.bio.data.strip(), avatar_path, session["user_id"]),
        )
        db.commit()
        log_audit("profile.updated", "user", session["user_id"], {})
        flash("Profile updated.", "success")
        return redirect(url_for("main.profile"))
    return render_template("profile.html", form=form, user=user)


@main_bp.route("/uploads/<path:filename>")
def upload_file(filename):
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    requested = Path(filename)
    if requested.is_absolute() or ".." in requested.parts:
        abort(404)
    if not str(requested).startswith("avatars/"):
        abort(404)
    if requested.suffix.lower() not in current_app.config["ALLOWED_UPLOAD_EXTENSIONS"]:
        abort(404)
    return send_from_directory(upload_folder, str(requested))


@main_bp.route("/password-reset/request", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["PASSWORD_RESET_RATE_LIMIT"])
def password_reset_request():
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        db = get_db()
        user = db.execute(
            "SELECT id FROM users WHERE username = ?",
            (form.username.data.strip(),),
        ).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc) + timedelta(minutes=30)
            token_hash = _token_hash(token)
            db.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (user["id"], token_hash, expires.isoformat()),
            )
            db.commit()
            log_audit("password.reset.requested", "user", user["id"], {})
        flash("If the account exists, a reset link has been generated.", "success")
    return render_template("password_reset_request.html", form=form)


@main_bp.route("/password-reset/<token>", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["PASSWORD_RESET_RATE_LIMIT"])
def password_reset(token):
    form = PasswordResetForm()
    db = get_db()
    token_row = db.execute(
        """
        SELECT * FROM password_reset_tokens
        WHERE token_hash = ? AND used_at IS NULL
        """,
        (_token_hash(token),),
    ).fetchone()
    if not token_row:
        legacy_row = db.execute(
            """
            SELECT * FROM password_reset_tokens
            WHERE token = ? AND used_at IS NULL
            """,
            (token,),
        ).fetchone()
        if legacy_row:
            token_row = legacy_row
            db.execute(
                "UPDATE password_reset_tokens SET token_hash = ?, token = NULL WHERE id = ?",
                (_token_hash(token), token_row["id"]),
            )
            db.commit()

    if not token_row:
        flash("Invalid or expired token.", "error")
        return redirect(url_for("main.login"))
    expires_at = datetime.fromisoformat(token_row["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        flash("Token expired.", "error")
        return redirect(url_for("main.password_reset_request"))

    if form.validate_on_submit():
        password_ok, password_result = validate_password(form.password.data)
        if not password_ok:
            flash(password_result, "error")
            return redirect(url_for("main.password_reset", token=token))
        db.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (generate_password_hash(password_result), token_row["user_id"]),
        )
        db.execute(
            "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (token_row["id"],),
        )
        db.commit()
        log_audit("password.reset.completed", "user", token_row["user_id"], {})
        flash("Password updated. Please log in.", "success")
        return redirect(url_for("main.login"))
    return render_template("password_reset.html", form=form)


@main_bp.route("/groups")
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def groups():
    if not is_logged_in():
        return redirect(url_for("main.login"))
    db = get_db()
    group_list = db.execute(
        """
        SELECT g.id, g.name, gm.role
        FROM group_chats g
        JOIN group_members gm ON gm.group_id = g.id
        WHERE gm.user_id = ?
        ORDER BY g.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()
    # TODO(phase-4): add create/invite/kick endpoints with role-based checks and moderation hooks.
    return render_template("groups.html", groups=group_list)


@main_bp.route("/logout", methods=["POST"])
@limiter.limit(lambda: current_app.config["SENSITIVE_RATE_LIMIT"])
def logout():
    _touch_presence(False)
    session.clear()
    return redirect(url_for("main.login"))


@socketio.on("connect")
def socket_connect():
    if not is_logged_in():
        return False
    join_room(f"user_{session['user_id']}")
    emit("connected", {"ok": True, "user_id": session["user_id"]})


@socketio.on("open_chat")
def handle_open_chat(data):
    if not is_logged_in():
        return {"ok": False, "error": "Please log in again."}

    chat_user_id = data.get("chat_user_id")
    try:
        chat_user_id = int(chat_user_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid chat user."}

    join_room(_conversation_room(session["user_id"], chat_user_id))
    delivered_ids = _mark_delivered(chat_user_id, notify=True)
    seen_ids = _mark_seen(chat_user_id, notify=True)
    return {"ok": True, "delivered_ids": delivered_ids, "seen_ids": seen_ids}


@socketio.on("message_received")
def handle_message_received(data):
    if not is_logged_in():
        return {"ok": False, "error": "Please log in again."}

    message_id = data.get("message_id")
    status = (data.get("status") or "").strip().lower()
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid message id."}

    if status not in {"delivered", "seen"}:
        return {"ok": False, "error": "Invalid message status."}

    _mark_single_message_status(message_id, status)
    return {"ok": True}


@socketio.on("send_message")
def handle_send_message(data):
    if not is_logged_in():
        return {"ok": False, "error": "Please log in again."}

    receiver_id = data.get("receiver_id")
    message = (data.get("message") or "").strip()
    client_nonce = (data.get("client_nonce") or "").strip()[:64]

    if not receiver_id or not message:
        return {"ok": False, "error": "Message cannot be empty."}

    try:
        receiver_id = int(receiver_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid recipient."}

    message_ok, message_or_error = validate_message(
        message, current_app.config["MAX_MESSAGE_LENGTH"]
    )
    if not message_ok:
        return {"ok": False, "error": message_or_error}

    payload = _create_message(
        session["user_id"], receiver_id, message_or_error, client_nonce=client_nonce or None
    )
    if not payload:
        return {"ok": False, "error": "User not found."}

    log_audit("message.sent", "message", payload["id"], {"receiver_id": receiver_id})
    _broadcast_message(payload)
    return {"ok": True, "message": payload}
