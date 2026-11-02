"""A stand-in for Supabase Auth, for local end-to-end checks of the password sign-in (spec 0010).

It answers the few endpoints the dashboard and the core use: /token (password sign-in and
refresh), /user, /logout and the JWKS. It signs ES256 tokens with a key made fresh each run,
the way a project on asymmetric signing keys does. Local tests only.

    uv run python scripts/dev/fake_supabase_auth.py --user '<uuid>:riley@example.test:a-long-password'
    uv run python scripts/dev/stack.py --changes --supabase-url http://127.0.0.1:54321
"""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

KID = f"fake-{secrets.token_hex(4)}"
KEY = ec.generate_private_key(ec.SECP256R1())


class Auth:
    def __init__(self, base: str, expires: int) -> None:
        self.base, self.expires = base, expires
        self.users: dict[str, str] = {}         # email -> user id
        self.passwords: dict[str, str] = {}     # email -> password
        self.refresh: dict[str, str] = {}       # refresh token -> email, rotated on use

    def user(self, email: str) -> dict:
        now = datetime.now(UTC).isoformat()
        return {"id": self.users[email], "aud": "authenticated", "role": "authenticated", "email": email,
                "email_confirmed_at": now, "app_metadata": {"provider": "email", "providers": ["email"]},
                "user_metadata": {}, "identities": [], "created_at": now, "updated_at": now, "is_anonymous": False}

    def session(self, email: str) -> dict:
        now = int(time.time())
        claims = {"iss": f"{self.base}/auth/v1", "sub": self.users[email], "aud": "authenticated", "role": "authenticated",
                  "email": email, "iat": now, "exp": now + self.expires, "session_id": str(uuid.uuid4()),
                  "is_anonymous": False, "aal": "aal1", "amr": [{"method": "password", "timestamp": now}]}
        refresh = secrets.token_urlsafe(24)
        self.refresh[refresh] = email
        return {"access_token": jwt.encode(claims, KEY, algorithm="ES256", headers={"kid": KID}), "token_type": "bearer",
                "expires_in": self.expires, "expires_at": now + self.expires, "refresh_token": refresh, "user": self.user(email)}


def handler(auth: Auth) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict | None = None) -> None:
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/auth/v1/.well-known/jwks.json":
                public = jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
                return self._send(200, {"keys": [{**public, "kid": KID, "alg": "ES256", "use": "sig", "key_ops": ["verify"]}]})
            if url.path == "/auth/v1/user":
                try:
                    claims = jwt.decode(self.headers.get("Authorization", "")[7:], KEY.public_key(), algorithms=["ES256"],
                                        audience="authenticated")
                except jwt.PyJWTError:
                    return self._send(401, {"code": "bad_jwt", "msg": "invalid JWT"})
                return self._send(200, auth.user(claims["email"]))
            return self._send(404, {"code": "not_found", "msg": url.path})

        def do_POST(self) -> None:
            url, body = urlparse(self.path), self._body()
            if url.path == "/_fake/users":
                auth.users[body["email"].lower()] = body["id"]
                auth.passwords[body["email"].lower()] = body["password"]
                return self._send(200, {})
            grant = parse_qs(url.query).get("grant_type", [""])[0]
            if url.path == "/auth/v1/token" and grant == "password":
                email = str(body.get("email", "")).lower()
                known = auth.passwords.get(email)
                # Same answer for an unknown email and a wrong password, as Supabase gives.
                if known is None or not hmac.compare_digest(known, str(body.get("password", ""))):
                    return self._send(400, {"code": "invalid_credentials", "msg": "Invalid login credentials"})
                return self._send(200, auth.session(email))
            if url.path == "/auth/v1/token" and grant == "refresh_token":
                email = auth.refresh.pop(str(body.get("refresh_token", "")), None)
                if email is None:
                    return self._send(400, {"code": "refresh_token_not_found", "msg": "Invalid Refresh Token"})
                return self._send(200, auth.session(email))
            if url.path == "/auth/v1/logout":  # revoke every refresh token of the bearer, as scope=global does
                try:
                    email = jwt.decode(self.headers.get("Authorization", "")[7:], KEY.public_key(), algorithms=["ES256"],
                                       audience="authenticated")["email"]
                except jwt.PyJWTError:
                    return self._send(401, {"code": "bad_jwt", "msg": "invalid JWT"})
                for token in [t for t, owner in auth.refresh.items() if owner == email]:
                    del auth.refresh[token]
                return self._send(204)
            return self._send(404, {"code": "not_found", "msg": url.path})

        def log_message(self, fmt: str, *args) -> None:
            print(f"fake supabase: {fmt % args}", flush=True)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=54321)
    parser.add_argument("--user", action="append", default=[], help="<uuid>:<email>:<password>, repeatable")
    parser.add_argument("--expires", type=int, default=3600, help="access-token lifetime in seconds")
    args = parser.parse_args()
    auth = Auth(f"http://127.0.0.1:{args.port}", args.expires)
    for entry in args.user:
        user_id, email, password = entry.split(":", 2)
        auth.users[email.lower()] = str(uuid.UUID(user_id))
        auth.passwords[email.lower()] = password
    print(f"fake Supabase Auth on http://127.0.0.1:{args.port} (key {KID})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(auth)).serve_forever()


if __name__ == "__main__":
    main()
