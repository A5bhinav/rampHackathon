"""A read-only reader for the Ramp mock's developer API (spec 0041). Standard library only.

The token is minted as the mock's README documents (`dialback_ramp_mock.tokens.mint` is the reference):
`mock.<dataset_id>.<hex HMAC-SHA256(MOCK_RAMP_SECRET, dataset_id)>`. Every request is a GET, and the
token is only ever sent to the configured API's own host.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

API = "/developer/v1"


class RampError(Exception):
    """The API could not be read: a refused request, a bad answer, or a link to another host."""


def mint_token(secret: bytes, dataset_id: uuid.UUID) -> str:
    return f"mock.{dataset_id}.{hmac.new(secret, str(dataset_id).encode(), hashlib.sha256).hexdigest()}"


def reader(base_url: str, token: str, timeout: float = 10.0) -> Callable[[str], dict[str, Any]]:
    """`get(target)` for a path under the API, or a page link the API returned on the same host."""
    base = urlsplit(base_url.rstrip("/"))
    if base.scheme not in ("http", "https") or not base.netloc:
        raise RampError(f"not an http(s) URL: {base_url!r}")

    def get(target: str) -> dict[str, Any]:
        link = urlsplit(target)
        if link.scheme or link.netloc:
            if (link.scheme, link.netloc) != (base.scheme, base.netloc):
                raise RampError("refusing a page link to another host")
            url = target
        else:
            url = base.geturl() + target
        request = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RampError(f"{exc.code} from {link.path}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise RampError(f"can't reach the API: {exc}") from None
        except ValueError:
            raise RampError(f"not JSON from {link.path}") from None
        if not isinstance(body, dict):
            raise RampError(f"not a JSON object from {link.path}")
        return body

    return get
