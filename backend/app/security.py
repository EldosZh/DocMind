import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from backend.app.config import settings

passwords = PasswordHash.recommended()
def hash_password(value: str) -> str: return passwords.hash(value)
def verify_password(value: str, encoded: str) -> bool: return passwords.verify(value, encoded)
def issue_access(user_id: UUID) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "exp": expiry, "type": "access"}, settings.secret_key, algorithm="HS256")
def decode_access(token: str) -> UUID:
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise ValueError("Invalid token type")
    return UUID(payload["sub"])
def new_refresh():
    token = secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expiry = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    return token, digest, expiry
def refresh_digest(token: str): return hashlib.sha256(token.encode()).hexdigest()
