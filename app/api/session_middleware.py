import base64
import binascii
import hashlib
import hmac
import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SignedSessionMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        secret_key: str,
        session_cookie: str = "session",
        max_age: int = 14 * 24 * 60 * 60,
        same_site: str = "lax",
        https_only: bool = False,
    ) -> None:
        super().__init__(app)
        self.secret_key = secret_key.encode("utf-8")
        self.session_cookie = session_cookie
        self.max_age = max_age
        self.same_site = same_site
        self.https_only = https_only

    async def dispatch(self, request: Request, call_next) -> Response:
        raw_cookie = request.cookies.get(self.session_cookie)
        request.scope["session"] = self._load_session(raw_cookie)

        response = await call_next(request)

        session_payload = request.scope.get("session", {})
        if session_payload:
            response.set_cookie(
                self.session_cookie,
                self._dump_session(session_payload),
                max_age=self.max_age,
                path="/",
                httponly=True,
                samesite=self.same_site,
                secure=self.https_only,
            )
        elif raw_cookie is not None:
            response.delete_cookie(self.session_cookie, path="/")

        return response

    def _dump_session(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded_payload = base64.urlsafe_b64encode(serialized).decode("ascii")
        signature = hmac.new(self.secret_key, serialized, hashlib.sha256).hexdigest()
        return f"{encoded_payload}.{signature}"

    def _load_session(self, raw_cookie: str | None) -> dict[str, Any]:
        if not raw_cookie:
            return {}

        try:
            encoded_payload, signature = raw_cookie.split(".", 1)
            serialized = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return {}

        expected_signature = hmac.new(self.secret_key, serialized, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return {}

        try:
            payload = json.loads(serialized.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

        return payload if isinstance(payload, dict) else {}
