"""
SSRF guard for outbound fetches that use caller/scrape-supplied URLs
(the playback proxy and host extractors).

Two layers:
  1. guard_url() — cheap up-front scheme + literal-host check that also
     normalises sneaky IPv4 encodings (decimal 2130706433, hex 0x7f000001,
     octal, IPv4-mapped IPv6) so tricks like http://2130706433/ can't smuggle
     127.0.0.1 past the check.

  2. guarded DNS resolution via is_private_address() — called before every
     socket connect (including redirect hops) to block DNS rebinding attacks
     where a public hostname resolves to a private IP.

Mirrors StreamPlay's src/utils/ssrf.ts logic exactly, adapted for Python/httpx.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import urlparse


# ── IP classification ─────────────────────────────────────────────────────────

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918
    ipaddress.ip_network("169.254.0.0/16"),   # link-local + cloud metadata (169.254.169.254)
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT (RFC6598)
    ipaddress.ip_network("0.0.0.0/8"),        # unspecified
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("::/128"),            # IPv6 unspecified
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
]


def _parse_sneaky_ipv4(host: str) -> Optional[ipaddress.IPv4Address]:
    """
    Parse bare IPv4 in dotted / decimal / hex / octal form.
    Handles the normalisation tricks that bypass naive checks:
      - 2130706433 → 127.0.0.1
      - 0x7f000001 → 127.0.0.1
      - 017700000001 → 127.0.0.1
    Returns None if not a recognisable IPv4 literal.
    """
    h = host.strip().rstrip(".")  # strip trailing dot (FQDN form)

    # Dotted quad — standard and most common
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", h):
        try:
            return ipaddress.IPv4Address(h)
        except ValueError:
            return None

    # Single-integer forms
    try:
        if h.startswith("0x") or h.startswith("0X"):
            n = int(h, 16)
        elif h.startswith("0") and len(h) > 1 and h[1:].isdigit():
            n = int(h, 8)
        elif h.isdigit():
            n = int(h)
        else:
            return None
        if 0 <= n <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(n)
    except (ValueError, OverflowError):
        pass
    return None


def is_private_address(addr: str) -> bool:
    """
    True if `addr` (already resolved to an IP string) points somewhere internal.
    Handles IPv4-mapped IPv6 (::ffff:127.0.0.1) transparently.
    """
    h = addr.lower().lstrip("[").rstrip("]")
    # Strip zone id (fe80::1%eth0)
    h = h.split("%")[0]

    # IPv4-mapped IPv6: ::ffff:a.b.c.d or ::ffff:7f00:0001
    mapped_dot = re.match(r"^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$", h, re.I)
    mapped_hex = re.match(r"^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$", h, re.I)
    if mapped_dot:
        h = mapped_dot.group(1)
    elif mapped_hex:
        hi = int(mapped_hex.group(1), 16)
        lo = int(mapped_hex.group(2), 16)
        h = f"{(hi >> 8) & 255}.{hi & 255}.{(lo >> 8) & 255}.{lo & 255}"

    # Try sneaky-encoded IPv4 first
    sneaky = _parse_sneaky_ipv4(h)
    if sneaky is not None:
        return any(sneaky in net for net in _PRIVATE_NETWORKS if net.version == 4)

    try:
        ip = ipaddress.ip_address(h)
        return any(ip in net for net in _PRIVATE_NETWORKS if net.version == ip.version)
    except ValueError:
        return False


def is_blocked_host(host: str) -> bool:
    """True if a hostname (or IP literal) must never be fetched server-side."""
    h = host.lower().lstrip("[").rstrip("]")
    if not h:
        return True
    if h in ("localhost",) or h.endswith(".localhost") or h.endswith(".local") or h.endswith(".internal"):
        return True
    return is_private_address(h)


SSRF_BLOCKED = "SSRF_BLOCKED"


def guard_url(raw: str) -> Optional[str]:
    """
    Validate a caller-supplied URL.
    Returns a human-readable reason string when blocked, else None.
    """
    try:
        u = urlparse(raw)
    except Exception:
        return "bad url"
    if u.scheme not in ("http", "https"):
        return "blocked scheme"
    if not u.hostname:
        return "bad url"
    if is_blocked_host(u.hostname):
        return "blocked host"
    return None


async def guard_resolved_url(url: str) -> Optional[str]:
    """
    Like guard_url() but also resolves the hostname via DNS and checks every
    returned address. This catches DNS rebinding where a hostname resolves to
    a private IP even though the URL looks public.

    Returns a reason string if blocked, else None.
    """
    reason = guard_url(url)
    if reason:
        return reason

    try:
        u = urlparse(url)
        hostname = u.hostname or ""
        # Try to resolve and check all addresses
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            addr = info[4][0]
            if is_private_address(addr):
                return f"blocked private address {addr}"
    except OSError:
        pass  # DNS failure is not an SSRF issue; let httpx handle it naturally

    return None
