"""
Authentication: password hashing + JWT issuance/verification.

Design:
- Passwords hashed with bcrypt (via passlib) - never stored in plain text.
- On successful login/register, we issue a short-lived JWT access token
  signed with JWT_SECRET_KEY. The frontend stores it and sends it as
  `Authorization: Bearer <token>` on every subsequent request.
- `get_current_user` is a FastAPI dependency that decodes the token,
  loads the UserProfile, and 401s if anything is invalid/expired -
  this replaces the old "trust whatever user_id you're given" pattern.
"""
import os
import secrets
import hashlib
import datetime as dt
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db import get_session, UserProfile

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-only-insecure-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # 24h default

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int, email: str) -> str:
    expire = dt.datetime.utcnow() + dt.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_session),
) -> UserProfile:
    """FastAPI dependency: resolves the authenticated user from the
    Bearer token. Use this instead of trusting a raw `user_id` form
    field - every protected endpoint should depend on this."""
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated. Please log in.")
    payload = decode_access_token(token)
    user_id = int(payload.get("sub"))
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists. Please log in again.")
    return user


# ---------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------
# Design: generate a random URL-safe token, email the RAW token to the
# user, but only ever store its SHA-256 hash + an expiry in the DB -
# same principle as password hashing, so a DB leak alone can't be used
# to reset anyone's password. Tokens are single-use (cleared on
# successful reset) and short-lived (30 min default).

RESET_TOKEN_EXPIRE_MINUTES = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", "30"))


def generate_reset_token() -> tuple[str, str, dt.datetime]:
    """Returns (raw_token_for_email, hash_to_store, expiry). The raw
    token is only ever held in memory long enough to put it in the
    email - never written to the DB or logs."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires = dt.datetime.utcnow() + dt.timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    return raw_token, token_hash, expires


def verify_reset_token(raw_token: str, stored_hash: str, expires_at: dt.datetime) -> bool:
    if not stored_hash or not expires_at:
        return False
    if dt.datetime.utcnow() > expires_at:
        return False
    incoming_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return secrets.compare_digest(incoming_hash, stored_hash)
