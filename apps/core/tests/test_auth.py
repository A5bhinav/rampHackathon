"""Who may call the dashboard API (spec 0010). The core trusts a token only when its signature
checks out against a key source it was configured with: the Supabase project's public signing
keys (JWKS), or a shared HS256 secret (local dev, or a project still on the legacy secret)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid

import httpx
import jwt
import pytest
from conftest import JWT_SECRET, core_settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from dialback_core.app import create_app
from dialback_core.auth import SigningKeys, TokenRejected, TokenVerifier, verifier_from_settings
from dialback_core.settings import ConfigurationError, Settings

pytestmark = pytest.mark.anyio
PROJECT = "https://abcdefghijklmnop.supabase.co"
ISSUER = f"{PROJECT}/auth/v1"
JWKS_URL = f"{PROJECT}/auth/v1/.well-known/jwks.json"


class Project:
    """A stand-in Supabase project: its private keys sign tokens; its JWKS lists the public halves."""

    def __init__(self) -> None:
        self.private: dict[str, tuple[str, object]] = {}
        self.fetches = 0
        self.down = False
        self.add("ec-1", "ES256")

    def add(self, kid: str, alg: str) -> None:
        key = ec.generate_private_key(ec.SECP256R1()) if alg == "ES256" else rsa.generate_private_key(65537, 2048)
        self.private[kid] = (alg, key)

    def jwks(self) -> dict:
        keys = []
        for kid, (alg, key) in self.private.items():
            algo = jwt.algorithms.ECAlgorithm if alg == "ES256" else jwt.algorithms.RSAAlgorithm
            keys.append({**algo.to_jwk(key.public_key(), as_dict=True), "kid": kid, "alg": alg, "use": "sig"})
        return {"keys": keys}

    async def fetch(self, url: str) -> dict:
        assert url == JWKS_URL
        self.fetches += 1
        await asyncio.sleep(0.01)  # a real fetch yields, so concurrent requests overlap
        if self.down:
            raise httpx.ConnectError("Supabase is unreachable")
        return self.jwks()

    def token(self, user: uuid.UUID, kid: str = "ec-1", ttl: int = 600, **claims) -> str:
        alg, key = self.private[kid]
        body = {"sub": str(user), "aud": "authenticated", "role": "authenticated", "iss": ISSUER,
                "exp": int(time.time()) + ttl, "is_anonymous": False, **claims}
        body = {k: v for k, v in body.items() if v is not None}  # a None claim is left out
        return jwt.encode(body, key, algorithm=alg, headers={"kid": kid})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def project() -> Project:
    return Project()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def verifier(project: Project, clock: Clock, secret: bytes | None = None) -> TokenVerifier:
    keys = SigningKeys(JWKS_URL, fetch=project.fetch, clock=clock)
    return TokenVerifier(audience="authenticated", secret=secret, keys=keys, issuer=ISSUER)


def hand_made(header: dict, body: dict, key: bytes | None) -> str:
    """A token built by hand, for the forgeries PyJWT itself refuses to make."""
    def seg(d: dict) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=")
    signing_input = seg(header) + b"." + seg(body)
    sig = b"" if key is None else base64.urlsafe_b64encode(hmac.new(key, signing_input, hashlib.sha256).digest()).rstrip(b"=")
    return (signing_input + b"." + sig).decode()


async def rejected(v: TokenVerifier, token: str) -> bool:
    try:
        await v.verify(token)
    except TokenRejected:
        return True
    return False


async def test_a_token_signed_with_the_projects_key_names_the_person(project, clock):
    user = uuid.uuid4()
    principal = await verifier(project, clock).verify(project.token(user))
    assert principal.user_id == user and principal.claims["iss"] == ISSUER


async def test_an_rsa_signing_key_works_too(project, clock):
    project.add("rsa-1", "RS256")
    user = uuid.uuid4()
    assert (await verifier(project, clock).verify(project.token(user, kid="rsa-1"))).user_id == user


@pytest.mark.parametrize("change", [
    {"ttl": -30}, {"aud": "anon"}, {"iss": "https://someone-else.supabase.co/auth/v1"}, {"iss": None},
    {"role": "service_role"}, {"role": "anon"}, {"is_anonymous": True}, {"sub": "not-a-uuid"},
], ids=["expired", "wrong-audience", "wrong-issuer", "no-issuer", "service-role", "anon-role", "anonymous-sign-in", "bad-subject"])
async def test_a_token_that_does_not_name_a_signed_in_person_of_this_project_is_rejected(project, clock, change):
    claims = dict(change)
    ttl = claims.pop("ttl", 600)
    token = project.token(uuid.uuid4(), ttl=ttl, **claims)
    assert await rejected(verifier(project, clock), token)


async def test_only_the_configured_key_sources_are_trusted(project, clock):
    user = uuid.uuid4()
    body = {"sub": str(user), "aud": "authenticated", "role": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600}
    jwks_only = verifier(project, clock)
    # A dev-style token signed with a shared secret is refused when the core only trusts the JWKS.
    assert await rejected(jwks_only, jwt.encode(body, JWT_SECRET, algorithm="HS256"))
    # The classic confusion attack: HS256 "signed" with the public key's PEM. Refused.
    public_pem = project.private["ec-1"][1].public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert await rejected(jwks_only, hand_made({"alg": "HS256", "typ": "JWT", "kid": "ec-1"}, body, public_pem))
    assert await rejected(verifier(project, clock, secret=JWT_SECRET),
                          hand_made({"alg": "HS256", "typ": "JWT", "kid": "ec-1"}, body, public_pem))
    # Unsigned tokens are never accepted.
    assert await rejected(jwks_only, hand_made({"alg": "none", "typ": "JWT", "kid": "ec-1"}, body, None))
    # With the legacy secret configured as well, an HS256 token from that project is accepted.
    both = verifier(project, clock, secret=JWT_SECRET)
    assert (await both.verify(jwt.encode(body, JWT_SECRET, algorithm="HS256"))).user_id == user


async def test_a_token_whose_header_algorithm_does_not_match_its_key_is_rejected(project, clock):
    project.add("rsa-1", "RS256")
    _, rsa_key = project.private["rsa-1"]
    token = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated", "iss": ISSUER,
                        "exp": int(time.time()) + 600}, rsa_key, algorithm="RS256", headers={"kid": "ec-1"})
    assert await rejected(verifier(project, clock), token), "an RS256 token can't claim the EC key's id"


async def test_keys_are_cached_and_an_unknown_key_id_refetches_at_most_once_in_a_while(project, clock):
    v = verifier(project, clock)
    user = uuid.uuid4()
    for _ in range(5):
        await v.verify(project.token(user))
    assert project.fetches == 1, "one fetch serves many requests"

    # A made-up key id refetches once; more made-up ids right after don't hammer Supabase.
    stranger = Project()
    stranger.private["nope-1"] = stranger.private.pop("ec-1")
    clock.now += 31
    assert await rejected(v, stranger.token(user, kid="nope-1"))
    assert await rejected(v, stranger.token(user, kid="nope-1"))
    assert project.fetches == 2

    # Supabase rotates in a new key: once the refetch window passes, the new key is found.
    project.add("ec-2", "ES256")
    clock.now += 31
    assert (await v.verify(project.token(user, kid="ec-2"))).user_id == user
    assert project.fetches == 3


async def test_an_unreachable_jwks_keeps_recent_keys_and_then_fails_closed(project, clock):
    v = verifier(project, clock)
    user = uuid.uuid4()
    await v.verify(project.token(user))
    project.down = True
    clock.now += 601  # a refresh is due, but Supabase can't be reached: the recent keys still work
    assert (await v.verify(project.token(user))).user_id == user
    clock.now += 600  # past the 20 minutes a key set is trusted without a successful refresh
    assert await rejected(v, project.token(user)), "fail closed: no fresh keys, no access"
    fresh = verifier(project, clock)
    assert await rejected(fresh, project.token(user)), "with no keys ever fetched, nothing is accepted"


BASE_ENV = {"DATABASE_URL": "postgresql://x", "RAMP_BASE_URL": "http://mock", "MOCK_RAMP_SECRET": "m" * 16,
            "RAMP_WEBHOOK_SECRET": "w" * 16, "FINGERPRINT_KEY": "f" * 32, "DIGITS_KEY": "d" * 32, "INTERNAL_API_TOKEN": "i" * 24}


def test_settings_need_a_key_source_and_derive_the_project_urls():
    with pytest.raises(ConfigurationError, match="SUPABASE_URL"):
        Settings.from_env(BASE_ENV)
    s = Settings.from_env({**BASE_ENV, "SUPABASE_URL": f"  {PROJECT}/ "})
    assert s.supabase_url == PROJECT and s.jwt_secret == b""
    v = verifier_from_settings(s)
    assert v.issuer == ISSUER and v.keys is not None and v.keys.url == JWKS_URL and v.secret is None
    dev = Settings.from_env({**BASE_ENV, "DIALBACK_JWT_SECRET": "s" * 32})
    v = verifier_from_settings(dev)
    assert v.keys is None and v.issuer is None and v.secret == b"s" * 32


@pytest.mark.parametrize("url", [
    "http://abcdefghijklmnop.supabase.co", "http://127.0.0.1:54321", f"{PROJECT}/auth/v1", f"{PROJECT}/?x=1",
    "https://localhost:@evil.example", "https://user:pw@abcdefghijklmnop.supabase.co", "/", "https://",
], ids=["http", "local-http", "with-path", "with-query", "userinfo-trick", "userinfo", "slash", "no-host"])
def test_supabase_url_must_be_the_projects_https_address_alone(url):
    with pytest.raises(ConfigurationError, match="SUPABASE_URL must be"):
        Settings.from_env({**BASE_ENV, "SUPABASE_URL": url, "DIALBACK_JWT_SECRET": "s" * 32})


def test_a_deployed_core_never_trusts_a_committed_secret_or_the_shared_secret_by_accident():
    for committed in ("dev-jwt-secret-0123456789abcdef0123456789", JWT_SECRET.decode()):
        with pytest.raises(ConfigurationError, match="committed"):
            Settings.from_env({**BASE_ENV, "DIALBACK_JWT_SECRET": committed})
    with pytest.raises(ConfigurationError, match="DIALBACK_TRUST_LEGACY_SECRET"):
        Settings.from_env({**BASE_ENV, "SUPABASE_URL": PROJECT, "DIALBACK_JWT_SECRET": "s" * 32})
    both = Settings.from_env({**BASE_ENV, "SUPABASE_URL": PROJECT, "DIALBACK_JWT_SECRET": "s" * 32, "DIALBACK_TRUST_LEGACY_SECRET": "1"})
    v = verifier_from_settings(both)
    assert v.secret == b"s" * 32 and v.issuer == ISSUER, "during a migration off the legacy secret, both are trusted"


async def test_with_both_sources_a_token_without_the_projects_issuer_is_still_refused(project, clock):
    user = uuid.uuid4()
    both = verifier(project, clock, secret=JWT_SECRET)
    body = {"sub": str(user), "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600}
    assert await rejected(both, jwt.encode(body, JWT_SECRET, algorithm="HS256")), "a dev token has no issuer"
    assert (await both.verify(jwt.encode({**body, "iss": ISSUER}, JWT_SECRET, algorithm="HS256"))).user_id == user


async def test_odd_headers_and_claims_are_handled(project, clock):
    v = verifier(project, clock)
    user = uuid.uuid4()
    body = {"sub": str(user), "aud": "authenticated", "role": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600}
    for header in ({"alg": ["ES256"], "kid": "ec-1"}, {"alg": "ES256"}, {"alg": "ES256", "kid": ["ec-1"]}):
        assert await rejected(v, hand_made(header, body, b"x")), f"refused, not a crash: {header}"
    assert (await v.verify(project.token(user, aud=["authenticated", "other"]))).user_id == user, "a list audience works"


async def test_concurrent_first_requests_fetch_the_keys_once_and_an_outage_recovers(project, clock):
    v = verifier(project, clock)
    users = [uuid.uuid4() for _ in range(10)]
    found = await asyncio.gather(*(v.verify(project.token(u)) for u in users))
    assert [p.user_id for p in found] == users and project.fetches == 1
    project.down = True
    clock.now += 1300  # the keys are past their 20 minutes and Supabase is down: nothing verifies
    assert await rejected(v, project.token(users[0]))
    project.down = False
    clock.now += 31  # Supabase is back; the next request after the refetch window gets fresh keys
    assert (await v.verify(project.token(users[0]))).user_id == users[0]


async def test_the_api_runs_as_the_person_a_supabase_token_names(world, project, clock):
    app = create_app(core_settings(), pool=world.pool, connector=world.connector, verifier=verifier(project, clock))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core") as core:
        me = await core.get("/api/me", headers={"Authorization": f"Bearer {project.token(world.users['approver1'])}"})
        assert me.status_code == 200 and me.json()["display_name"] == "Approver1" and "approver" in me.json()["roles"]
        dev = jwt.encode({"sub": str(world.users["approver1"]), "aud": "authenticated", "role": "authenticated",
                          "exp": int(time.time()) + 600}, JWT_SECRET, algorithm="HS256")
        refused = await core.get("/api/me", headers={"Authorization": f"Bearer {dev}"})
        assert refused.status_code == 401 and refused.json()["code"] == "unauthenticated"
