"""
utils/ssrf.py — SSRF guard for caller-supplied URLs.

Two layers:
  1. guard_url()          — cheap scheme + literal-host check, handles sneaky IPv4 encodings.
  2. guard_resolved_url() — also resolves DNS and blocks private addresses (DNS rebinding).

Only the proxy endpoints call these. Providers don't call external URLs with
caller-supplied input, so they don't need SSRF protection.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Optional
from urllib.parse import urlparse


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]


def _parse_sneaky_ipv4(host: str) -> Optional[ipaddress.IPv4Address]:
    h = host.strip().rstrip(".")
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", h):
        try:
            return ipaddress.IPv4Address(h)
        except ValueError:
            return None
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
    h = addr.lower().lstrip("[").rstrip("]").split("%")[0]
    mapped_dot = re.match(r"^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$", h, re.I)
    mapped_hex = re.match(r"^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$", h, re.I)
    if mapped_dot:
        h = mapped_dot.group(1)
    elif mapped_hex:
        hi = int(mapped_hex.group(1), 16)
        lo = int(mapped_hex.group(2), 16)
        h = f"{(hi >> 8) & 255}.{hi & 255}.{(lo >> 8) & 255}.{lo & 255}"

    sneaky = _parse_sneaky_ipv4(h)
    if sneaky is not None:
        return any(sneaky in net for net in _PRIVATE_NETWORKS if net.version == 4)

    try:
        ip = ipaddress.ip_address(h)
        return any(ip in net for net in _PRIVATE_NETWORKS if net.version == ip.version)
    except ValueError:
        return False


def is_blocked_host(host: str) -> bool:
    h = host.lower().lstrip("[").rstrip("]")
    if not h:
        return True
    if h in ("localhost",) or h.endswith(".localhost") or h.endswith(".local") or h.endswith(".internal"):
        return True
    return is_private_address(h)


def guard_url(raw: str) -> Optional[str]:
    """Returns a reason string if blocked, else None."""
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
    """Like guard_url() but also DNS-resolves the hostname."""
    reason = guard_url(url)
    if reason:
        return reason
    try:
        u = urlparse(url)
        hostname = u.hostname or ""
        infos = socket.getaddrinfo(hostname, None)
        for info in infos:
            addr = info[4][0]
            if is_private_address(addr):
                return f"blocked private address {addr}"
    except OSError:
        pass
    return None
