import re
from pathlib import Path

from werkzeug.datastructures import FileStorage


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")


def _detect_image_extension(sample_bytes):
    if sample_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if sample_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if sample_bytes.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if sample_bytes.startswith(b"RIFF") and sample_bytes[8:12] == b"WEBP":
        return ".webp"
    return None


def validate_username(username):
    value = (username or "").strip()
    if not USERNAME_RE.match(value):
        return False, "Username must be 3-30 chars (letters, numbers, underscore)."
    return True, value


def validate_password(password):
    value = (password or "").strip()
    if len(value) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", value):
        return False, "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", value):
        return False, "Password must include at least one lowercase letter."
    if not re.search(r"[0-9]", value):
        return False, "Password must include at least one number."
    return True, value


def validate_message(message, max_length):
    value = (message or "").strip()
    if not value:
        return False, "Message cannot be empty."
    if len(value) > max_length:
        return False, f"Message cannot exceed {max_length} characters."
    return True, value


def validate_upload(file_obj: FileStorage, allowed_extensions):
    if not file_obj or not file_obj.filename:
        return False, "No file selected."

    extension = Path(file_obj.filename).suffix.lower()
    if extension not in allowed_extensions:
        return False, "Unsupported file type."

    declared_type = (file_obj.mimetype or "").lower()
    if declared_type and not declared_type.startswith("image/"):
        return False, "Only image uploads are allowed."

    stream = file_obj.stream
    position = stream.tell()
    sample = stream.read(512)
    stream.seek(position)
    detected_ext = _detect_image_extension(sample)
    normalized_extension = ".jpg" if extension == ".jpeg" else extension
    if not detected_ext or detected_ext != normalized_extension:
        return False, "Uploaded file content does not match a supported image type."

    return True, extension
