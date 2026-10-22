"""Defense in depth for the "no data leaves the machine" rule.

1. docker-compose runs the scrubber with network_mode: none, so the container
   has only a loopback interface.
2. assert_isolated() refuses to start if any other interface exists.
3. install() patches the socket module so no internet socket can connect or
   resolve a hostname from inside this process, even by a library.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


class NetworkBlocked(RuntimeError):
    pass


_installed = False


def _blocked(*_args, **_kwargs):
    raise NetworkBlocked("Network access is disabled in Data Frenzy Local Scrubber")


def install() -> None:
    global _installed
    if _installed:
        return
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto

    def connect(self, address):
        if self.family == socket.AF_UNIX:
            return original_connect(self, address)
        _blocked()

    def connect_ex(self, address):
        if self.family == socket.AF_UNIX:
            return original_connect_ex(self, address)
        _blocked()

    def sendto(self, *args):
        if self.family == socket.AF_UNIX:
            return original_sendto(self, *args)
        _blocked()

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = _blocked
    socket.gethostbyname = _blocked
    socket.gethostbyname_ex = _blocked
    socket.create_connection = _blocked
    _installed = True


def network_interfaces() -> list[str] | None:
    """Interfaces visible to this process, or None if they can't be listed (non-Linux)."""
    net = Path("/sys/class/net")
    if not net.is_dir():
        return None
    return sorted(p.name for p in net.iterdir())


def assert_isolated() -> None:
    ifaces = network_interfaces()
    if ifaces is None:
        if os.environ.get("SCRUBBER_IN_CONTAINER"):
            raise SystemExit("Cannot confirm network isolation (no /sys/class/net). Refusing to run.")
        print("warning: not running in the container; network isolation not verified "
              "(sockets are still blocked in-process)", file=sys.stderr)
        return
    extra = [i for i in ifaces if i != "lo"]
    if extra:
        raise SystemExit(
            f"Refusing to run: network interfaces present ({', '.join(extra)}). "
            "Start the scrubber with `docker compose up`, which disables networking."
        )


def configure_offline_libraries() -> None:
    """Point libraries that would otherwise fetch data at their bundled copies.

    tldextract (used by Presidio's email recognizer) downloads the public
    suffix list on first use; use its bundled snapshot instead.
    """
    import tldextract
    import tldextract.tldextract as _tld

    offline = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, fallback_to_snapshot=True)
    tldextract.extract = offline
    _tld.extract = offline
