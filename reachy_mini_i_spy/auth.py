"""Authenticated, replay-resistant caregiver authority for the local app API."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request


class CaregiverGuard:
    """Bearer-bound, one-time CSRF authority for caregiver-only controls."""

    NONCE_TTL_SECONDS = 300.0
    MAX_NONCES = 32

    def __init__(self, token: str | None = None) -> None:
        self._token = token if token is not None else os.environ.get("REACHY_MINI_I_SPY_CAREGIVER_TOKEN", "")
        self._nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def _bearer_ok(self, request: Request) -> bool:
        authorization = request.headers.get("authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        return len(self._token) >= 32 and hmac.compare_digest(supplied, self._token)

    @staticmethod
    def _origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        return origin is None or hmac.compare_digest(origin.rstrip("/"), expected)

    def authenticated(self, request: Request) -> bool:
        return self._bearer_ok(request) and self._origin_ok(request)

    def issue(self, request: Request) -> str:
        if not self.authenticated(request):
            raise HTTPException(status_code=401, detail="Authenticated caregiver authority required")
        now = time.monotonic()
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._nonces = {value: expiry for value, expiry in self._nonces.items() if expiry > now}
            while len(self._nonces) >= self.MAX_NONCES:
                self._nonces.pop(min(self._nonces, key=self._nonces.get))
            self._nonces[nonce] = now + self.NONCE_TTL_SECONDS
        return nonce

    def authorize(self, request: Request, csrf_token: str | None) -> str:
        if not self.authenticated(request):
            raise HTTPException(status_code=401, detail="Authenticated caregiver authority required")
        now = time.monotonic()
        with self._lock:
            expiry = self._nonces.pop(csrf_token, None) if csrf_token else None
        if expiry is None or expiry <= now:
            raise HTTPException(status_code=403, detail="Fresh caregiver CSRF token required")
        return self.issue(request)
