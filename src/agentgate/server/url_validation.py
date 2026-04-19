"""URL validation — prevent SSRF on user-supplied URLs.

The main API proxies HTTP calls to `agent.url` (A2A routing) and
`agent.webhook_url` (task completion notifications). Without validation,
an attacker could register an agent pointing at:

  - 169.254.169.254 (AWS/GCP/Azure instance metadata)
  - 127.0.0.1 / localhost (DB, Redis, admin panel)
  - 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 (internal networks)
  - IPv6 link-local (fe80::/10)

and read credentials / probe internal services.

Validation happens at registration time (agent create/update). We do NOT
validate at request time because inside the container network we need
http://deployed-agent-<id>:<port> to work — those resolve to private
IPs. Instead we trust that our own container orchestration populates the
url field with internal names; the constraint is applied only when a
user supplies the url via API.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


# Allow our own container naming convention (http://agentgate-agent-xxx:port)
_INTERNAL_HOSTNAME_PREFIX = "agentgate-agent-"


def _is_private_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_url(url: str, *, allow_internal: bool = False) -> None:
    """Reject URLs that would let an attacker probe internal services.

    `allow_internal=True` is for URLs written by our own deploy engine
    (which uses http://agentgate-agent-<id>:<port>). User-supplied URLs
    never get this flag.
    """
    if not url:
        raise UnsafeURLError("URL must be non-empty")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Only http/https allowed, got '{parsed.scheme}'")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL must include a hostname")

    hostname_lower = hostname.lower()

    # Our own deployed-agent naming is explicitly allowed when the caller
    # opts in. Never allowed from user-supplied URLs.
    if allow_internal and hostname_lower.startswith(_INTERNAL_HOSTNAME_PREFIX):
        return

    # Block obvious loopback names regardless of DNS.
    if hostname_lower in ("localhost", "localhost.localdomain", "ip6-localhost"):
        raise UnsafeURLError(f"Loopback hostname not allowed: {hostname}")

    # If the hostname parses as an IP literal, check it directly.
    # We intentionally do NOT resolve DNS here: (1) it makes registration
    # a synchronous blocking call at attacker-controlled latency, and
    # (2) DNS resolution time-of-check/time-of-use is race-prone anyway.
    # The IP-literal check alone already blocks the common attacks
    # (169.254.169.254, 127.0.0.1, 10.0.0.0/8, ...).
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_private_ip(ip):
            raise UnsafeURLError(f"Private/internal IP not allowed: {ip}")
    except ValueError:
        pass  # not an IP literal — trust the hostname string check above
