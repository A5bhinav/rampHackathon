"""Who is calling the dashboard API (spec 0002, D9).

The dashboard sends the signed-in user's JWT: Supabase's access token in production, a
short-lived dev token locally (minted by the dashboard's server with the same secret). The
core verifies it the same way in both cases; there is no bypass. A valid token names a
person, and every query for that request runs in Postgres as that person.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    claims: dict[str, Any]


def verify(token: str, secret: bytes, audience: str) -> Principal:
    claims = jwt.decode(token, secret, algorithms=["HS256"], audience=audience,
                        options={"require": ["exp", "sub", "aud"]})
    return Principal(UUID(claims["sub"]), claims)


async def current_user(request: Request) -> Principal:
    settings = request.app.state.settings
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token or not settings.jwt_secret:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Sign in to continue."})
    try:
        return verify(token.strip(), settings.jwt_secret, settings.jwt_audience)
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Your session is not valid; sign in again."}) from exc
