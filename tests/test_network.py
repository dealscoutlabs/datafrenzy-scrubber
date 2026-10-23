"""Proof that the scrubber runs with no network at all."""

import os
import socket
from pathlib import Path

import pytest
import yaml

from scrubber import netguard

IN_CONTAINER = bool(os.environ.get("SCRUBBER_IN_CONTAINER"))
COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_compose_disables_networking_for_processing():
    compose = yaml.safe_load(COMPOSE.read_text())
    assert compose["services"]["scrubber"]["network_mode"] == "none"
    assert compose["services"]["tests"]["network_mode"] == "none"
    assert "ports" not in compose["services"]["scrubber"]


@pytest.mark.skipif(not IN_CONTAINER, reason="runs inside the network-less container")
def test_container_has_only_loopback():
    assert netguard.network_interfaces() == ["lo"]
    netguard.assert_isolated()  # does not raise


@pytest.mark.skipif(not IN_CONTAINER, reason="runs inside the network-less container")
def test_dns_and_tcp_fail_at_os_level():
    import _socket
    raw = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)  # bypasses our Python-level guard
    raw.settimeout(3)
    with pytest.raises(OSError):
        raw.connect(("1.1.1.1", 443))
    raw.close()
    with pytest.raises(OSError):
        _socket.getaddrinfo("example.com", 443)


def test_in_process_guard_blocks_internet_sockets():
    netguard.install()
    with pytest.raises(netguard.NetworkBlocked):
        socket.create_connection(("1.1.1.1", 443), timeout=1)
    with pytest.raises(netguard.NetworkBlocked):
        socket.getaddrinfo("example.com", 443)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(netguard.NetworkBlocked):
        s.connect(("93.184.216.34", 443))   # any internet address
    s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(netguard.NetworkBlocked):
        s.connect(("localhost", 9))         # host names never resolve
    s.close()


def test_loopback_allowed_for_the_computer_talking_to_itself():
    """Windows' event loop and the local review screen use 127.0.0.1; that never leaves the PC."""
    netguard.install()
    a, b = socket.socketpair()
    a.sendall(b"ok")
    assert b.recv(2) == b"ok"
    a.close()
    b.close()


def test_scrubbing_makes_no_network_attempts(scrubber, monkeypatch):
    attempts = []

    def spy(name):
        def record(*args, **kwargs):
            attempts.append((name, args[:2]))
            raise netguard.NetworkBlocked(name)
        return record

    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: spy("connect")(addr))
    monkeypatch.setattr(socket, "create_connection", spy("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", spy("getaddrinfo"))
    out = scrubber.scrub("Email jane.roe@example.com or visit https://www.example.org/a?b=1 from 10.0.0.1").text
    assert "jane.roe" not in out
    assert attempts == []
