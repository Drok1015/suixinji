import hashlib
import hmac
import secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"{salt}:{digest}"


def verify_password(password: str, encoded: str) -> bool:
    if ":" not in encoded:
        return False
    salt, _ = encoded.split(":", 1)
    return hmac.compare_digest(password_hash(password, salt), encoded)


def issue_session(secret: str) -> str:
    return URLSafeTimedSerializer(secret).dumps({"user": "owner"})


def valid_session(secret: str, token: str) -> bool:
    try:
        return URLSafeTimedSerializer(secret).loads(token, max_age=7 * 86400) == {"user": "owner"}
    except (BadSignature, SignatureExpired):
        return False
