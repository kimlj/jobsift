"""HTTP fetching for URLs we did not choose.

`enrich` follows a link that arrived in an email. Anything that can put a message
in the inbox can therefore pick a URL for this program to fetch — and `classify`
accepts mail on a subject keyword alone ("job alert", "hiring"), so the sender does
not even need to be a board we know. On a VPS that means anything reachable from
that box: `http://127.0.0.1:8080/admin`, `http://169.254.169.254/` for cloud
instance credentials, a printer, a database admin page.

The response is not shown to the sender, so this is a blind SSRF — but blind is
still enough to reach an endpoint that *acts* on a GET, and the fetched text is fed
straight to an LLM afterwards.

Four rules, in the order they matter:

1. **HTTPS only.** Measured on the real database: all 149 stored URLs are already
   https, so this costs nothing and removes plaintext redirect tampering.
2. **Public addresses only.** Every address the hostname resolves to must be a
   public one. One private address among several is a rejection, not a fallback.
3. **Pin to the address we validated.** Checking DNS and then handing the hostname
   to the HTTP client re-resolves it, and an attacker controlling the domain with a
   short TTL can answer differently the second time — the classic DNS rebinding
   race. So the request goes to the validated IP, with `Host` and TLS SNI set to
   the original hostname, which keeps certificate verification honest. There is no
   second lookup to poison.
4. **Re-validate every redirect hop.** A permitted host answering `302 ->
   http://169.254.169.254/` is the same attack wearing a hat.

**Two deliberate departures from the plan in TODO.md**, both because the literal
version would break something real:

* *"host allowlist"* — career-ops can pin to an allowlist because they have ~80
  known providers. We follow links from arbitrary boards to arbitrary employer
  sites; a mandatory allowlist would turn enrichment off. So the allowlist is
  optional (`filters.fetch.allow_hosts`, empty by default) and rule 2 is what
  actually defends.
* *"no cross-host redirects"* — cross-host redirects are the normal case here and
  one of them is a feature: `url.jobstreet.com` -> `ph.jobstreet.com` is what turns
  a 286-character tracking link into a 37-character one. Blocking them would undo
  that. Every hop is validated instead, which is the property that was wanted.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("https",)
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 15.0


class UnsafeURL(Exception):
    """The URL is one we refuse to fetch, with the reason as its message."""


# The well-known NAT64 prefix. An address inside it carries an IPv4 address in its
# low 32 bits, so 64:ff9b::7f00:1 IS 127.0.0.1 — and `is_global` says True for the
# whole prefix, which is the one place the stdlib's own answer is not the answer we
# need. Unwrapped explicitly below.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _unwrap(ip):
    """An IPv4 address hidden inside an IPv6 one, or the address unchanged.

    Three encodings do this — ::ffff:127.0.0.1 (v4-mapped), 2002:7f00:1:: (6to4)
    and 64:ff9b::7f00:1 (NAT64) — and each is a way of writing a private address
    that the IPv6 flags alone would wave through.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return ip
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def _address_is_public(raw: str) -> bool:
    """True only for an address on the public internet.

    `is_global` is the stdlib's own answer to this question and is the primary
    test: it is what knows that 100.64.0.0/10 (carrier-grade NAT) is not somewhere
    we should be fetching from, which none of is_private/is_reserved report. The
    explicit flags stay as a second opinion, because a wrong "yes" here is the
    whole vulnerability and a wrong "no" only skips one job.
    """
    try:
        ip = _unwrap(ipaddress.ip_address(raw))
    except ValueError:
        return False
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def resolve_public(host: str, port: int) -> str:
    """Every address `host` resolves to, checked; returns one to connect to.

    ALL of them must be public. A name that answers with both a public and a
    private address is a rejection — taking the public one would be trusting the
    order of a DNS reply chosen by whoever we are defending against.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve {host!r} ({exc})") from exc

    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise UnsafeURL(f"{host!r} resolved to nothing")
    for address in addresses:
        if not _address_is_public(address):
            raise UnsafeURL(f"{host!r} resolves to non-public address {address}")
    return addresses[0]


def check_url(url: str, allow_hosts: list[str] | None = None) -> tuple[str, str, int]:
    """Validate one URL. Returns (hostname, connect-address, port)."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme {parsed.scheme or '(none)'!r} is not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("no hostname")

    if allow_hosts:
        low = host.lower()
        if not any(low == h.lower() or low.endswith("." + h.lower()) for h in allow_hosts):
            raise UnsafeURL(f"host {host!r} is not in allow_hosts")

    port = parsed.port or 443
    try:
        # A URL written with a bare IP never reaches DNS, so check it directly.
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host, resolve_public(host, port), port
    if not _address_is_public(str(literal)):
        raise UnsafeURL(f"address {host} is not public")
    return host, str(literal), port


def safe_get(
    url: str,
    *,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allow_hosts: list[str] | None = None,
) -> httpx.Response:
    """GET a URL nobody trustworthy chose, following redirects one checked hop at a time.

    Raises UnsafeURL when any hop fails a rule. Other transport errors surface as
    the httpx exceptions the caller already handles.
    """
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            host, address, port = check_url(current, allow_hosts)
            parsed = urlparse(current)

            # Connect to the address just validated, not to the name. Host and SNI
            # carry the original hostname so virtual hosting and certificate
            # verification both still work.
            target = parsed._replace(
                netloc=f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
            ).geturl()
            request = client.build_request(
                "GET", target, headers={**(headers or {}), "Host": host}
            )
            request.extensions["sni_hostname"] = host

            response = client.send(request)
            if response.is_redirect and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                response.close()
                continue

            # The URL the caller should keep is the name-based one, not the pinned
            # address — it is what a human opens and what gets stored.
            response.request.url = httpx.URL(current)
            return response

    raise UnsafeURL(f"more than {max_redirects} redirects")
