"""Disposable-email-domain filter.

Three-layer cascade (cheap → expensive):

    1. Local list          ~195k domains loaded once at startup, O(1) set lookup
    2. Redis cache         7-day TTL of RapidAPI answers so we don't re-ask
    3. RapidAPI mailcheck  network fallback for domains neither list nor cache
                           knows about (100-1k free requests/month)

Design notes
------------
- The set is built once at import time, not per-request. Reload requires
  a restart (matches the "rebuild the image to refresh the list" policy).
- If the bundled file is missing (broken build) we fall back to a small
  hardcoded set of the most common throwaway providers.
- If Redis is down, we skip the cache and go straight to the API; if the
  API is down or not configured, we fail open (return False). Fail-open
  is deliberate: a legitimate user with a novel domain should not be
  blocked by network flakiness. The email verification step (which is
  always required) is the real second line of defence.
- Responses are logged at debug level only — never log the full email,
  only the domain part, to avoid accidental PII leaks.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from agentgate.core.config import settings

logger = logging.getLogger("agentgate.disposable")

REDIS_PREFIX = "disposable:api:"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
API_TIMEOUT = 3.0
API_HOST = "mailcheck.p.rapidapi.com"

# RFC 2606 reserves these for documentation/examples/tests. Some public
# disposable lists include them anyway; we strip them at runtime so test
# suites and docs can use them without surprises.
_RESERVED_DOMAINS: frozenset[str] = frozenset({
    "example.com", "example.net", "example.org",
    "test.com", "test", "localhost", "invalid", "example",
})

# Conservative fallback used only if the bundled list file is missing.
_FALLBACK_DOMAINS: frozenset[str] = frozenset({
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwam.com",
    "sharklasers.com", "guerrillamailblock.com", "grr.la", "guerrillamail.info",
    "guerrillamail.biz", "guerrillamail.de", "guerrillamail.net", "guerrillamail.org",
    "spam4.me", "trashmail.com", "trashmail.me", "trashmail.at", "trashmail.io",
    "yopmail.com", "yopmail.fr", "yopmail.net", "dispostable.com", "maildrop.cc",
    "mailnull.com", "spamgourmet.com", "spamgourmet.net", "spamgourmet.org",
    "trashmail.net", "trashmail.org", "trashmail.de", "10minutemail.com",
    "10minutemail.net", "10minutemail.org", "fakeinbox.com", "getnada.com",
    "mailtemp.info", "mohmal.com", "mytemp.email", "nada.ltd", "temp-mail.org",
    "temp-mail.ru", "tempmail.us.com", "throwaway.email", "tmailinator.com",
    "dolofan.com",
})


def _load_local_list() -> frozenset[str]:
    path = Path(settings.disposable_list_path)
    if not path.exists():
        logger.warning(
            "Disposable list %s not found — using %d-domain fallback",
            path, len(_FALLBACK_DOMAINS),
        )
        return _FALLBACK_DOMAINS
    domains: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                d = line.strip().lower()
                if d and not d.startswith("#"):
                    domains.add(d)
    except Exception:
        logger.exception("Failed to read disposable list from %s", path)
        return _FALLBACK_DOMAINS
    if len(domains) < 1000:
        logger.warning(
            "Disposable list looks suspiciously short (%d) — merging with fallback",
            len(domains),
        )
        domains |= _FALLBACK_DOMAINS
    logger.info("Loaded %d disposable domains from %s", len(domains), path)
    return frozenset(domains)


# Built once at module import.
_LOCAL_DOMAINS: frozenset[str] = _load_local_list()


def _domain_of(email: str) -> str | None:
    if "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


async def _check_cache(domain: str) -> bool | None:
    """Look up a cached RapidAPI answer in Redis. Returns True/False or None."""
    try:
        from agentgate.core.redis import get_redis

        r = get_redis()
        if not r:
            return None
        v = r.get(f"{REDIS_PREFIX}{domain}")
        if v is None:
            return None
        return v == "1" or v == b"1"
    except Exception:
        return None


async def _store_cache(domain: str, is_disposable: bool) -> None:
    try:
        from agentgate.core.redis import get_redis

        r = get_redis()
        if not r:
            return
        r.set(f"{REDIS_PREFIX}{domain}", "1" if is_disposable else "0", ex=CACHE_TTL_SECONDS)
    except Exception:
        pass


async def _check_rapidapi(domain: str) -> bool | None:
    """Ask RapidAPI mailcheck. Returns True/False or None (fail-open)."""
    if not settings.rapidapi_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            resp = await client.get(
                f"https://{API_HOST}/",
                params={"domain": domain},
                headers={
                    "X-RapidAPI-Key": settings.rapidapi_key,
                    "X-RapidAPI-Host": API_HOST,
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "RapidAPI returned %d for domain %s", resp.status_code, domain,
            )
            return None
        data = resp.json()
        return bool(data.get("disposable") or data.get("block"))
    except Exception as exc:
        logger.warning("RapidAPI call failed for %s: %s", domain, exc)
        return None


async def is_disposable(email: str) -> bool:
    """Return True if the email's domain is (or is likely) disposable.

    Order: local set → Redis cache → RapidAPI. Any layer that can't
    answer defers to the next one. If none can, returns False
    (fail-open) — the verification email will still be required before
    the account is usable.
    """
    domain = _domain_of(email)
    if not domain:
        return False

    # Reserved/test domains never count as disposable even if a noisy
    # upstream list tagged them.
    if domain in _RESERVED_DOMAINS:
        return False

    if domain in _LOCAL_DOMAINS:
        logger.debug("Domain %s matched local disposable list", domain)
        return True

    cached = await _check_cache(domain)
    if cached is not None:
        logger.debug("Domain %s matched cache (%s)", domain, cached)
        return cached

    api = await _check_rapidapi(domain)
    if api is None:
        return False  # fail open

    await _store_cache(domain, api)
    return api


def list_size() -> int:
    """Exposed for debugging / startup logs."""
    return len(_LOCAL_DOMAINS)
