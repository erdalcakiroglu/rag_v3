"""M-12 — Şifre hash'i + politika + email normalizasyonu (self-kayıt/giriş).

⚠ TOKEN'DAN FARKLI: Bearer token yüksek-entropilidir → sha256 (auth.hash_token) yeter.
Şifre DÜŞÜK-entropilidir → yavaş, salt'lı, bellek-sert hash ŞART: argon2id (PHC string).
Düz metin şifre ne DB'de ne log'da tutulur; bu modül hiçbir yere şifre YAZMAZ/loglamaz.

Enumeration savunması: bilinmeyen email'de de `dummy_verify` ile aynı argon2 maliyeti
ödenir (timing düz) ve çağıran nötr mesaj döner ("email veya şifre hatalı").
"""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

# OWASP uyumlu varsayılan parametreler (argon2-cffi default'u: argon2id).
_PH = PasswordHasher()

# Bilinmeyen kullanıcıda timing'i eşitlemek için sabit bir kukla hash (import'ta bir kez).
_DUMMY_HASH = _PH.hash("x" * 32)

# Kasıtlı olarak gevşek email deseni: doğrulama kapısı DOMAIN allowlist'tir, format değil.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    """Şifre → argon2id PHC string ($argon2id$...). Düz metin saklanmaz."""
    return _PH.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Sabit-zamanlı doğrulama. Hash yoksa (pending/None) kukla-verify ile timing eşitlenir
    ve False döner — 'kullanıcı var ama hash yok' ile 'yanlış şifre' ayırt edilemez."""
    if not password_hash:
        dummy_verify(password)
        return False
    try:
        return _PH.verify(password_hash, password)
    except Argon2Error:
        return False


def dummy_verify(password: str) -> None:
    """Bilinmeyen kullanıcı yolunda argon2 maliyetini öder (timing side-channel kapatır)."""
    try:
        _PH.verify(_DUMMY_HASH, password)
    except Argon2Error:
        pass


def normalize_email(email: str) -> str:
    """Email'i lower-normalize eder (DB'de lower(email) benzersiz — çift-kayıt önlenir)."""
    return (email or "").strip().lower()


def valid_email_format(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def email_domain(email: str) -> str:
    return normalize_email(email).rsplit("@", 1)[-1] if "@" in (email or "") else ""


def domain_allowed(email: str, allowed_domains: list[str]) -> bool:
    """FAIL-CLOSED: allowlist boşsa hiçbir email kabul edilmez. Karşılaştırma lower-normalize."""
    allow = {d.strip().lower() for d in (allowed_domains or []) if d and d.strip()}
    if not allow:
        return False
    return email_domain(email) in allow


def password_policy_error(password: str, *, min_length: int) -> str | None:
    """Politika ihlalini TR mesajla döndürür; uygunsa None. (Şu an: asgari uzunluk.)"""
    if not password or len(password) < int(min_length):
        return f"Şifre en az {int(min_length)} karakter olmalı."
    return None
