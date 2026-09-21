"""
Shared test configuration.

THE NETWORK IS BLOCKED IN TESTS
===============================
Every outbound HTTP call fails unless a test explicitly allows it.

This is here because it was needed. `fetch_candidates()` gained a traction
step that calls reddit, Hacker News and two RSS feeds; several tests exercise
that function for reasons unrelated to networking, and the suite quietly
started making over two hundred live requests per run. That is slow, it makes
green depend on four third-party services being up, and it points a CI job
that runs on every push at free APIs that never agreed to it.

A test that genuinely wants to exercise a request should patch the specific
client function it needs (`traction._get_json`, `sources._get`), which is what
the existing tests already do. If one truly needs a socket, it can ask for the
`allow_network` fixture - and should explain why in a comment.
"""

import socket

import pytest

_real_socket = socket.socket
_real_create_connection = socket.create_connection


class NetworkUsedInTests(RuntimeError):
    pass


def _blocked(*a, **k):
    raise NetworkUsedInTests(
        "A test tried to open a network connection.\n"
        "Patch the client function instead - traction._get_json, "
        "traction.requests.get, or sources._get - so the test is fast and "
        "does not depend on a third party being up. If a real socket is "
        "genuinely required, request the `allow_network` fixture and say why."
    )


@pytest.fixture(autouse=True)
def _no_network(request):
    if "allow_network" in request.fixturenames:
        yield
        return
    socket.socket = _blocked
    socket.create_connection = _blocked
    try:
        yield
    finally:
        socket.socket = _real_socket
        socket.create_connection = _real_create_connection


@pytest.fixture
def allow_network():
    """Opt back in. Use sparingly, and say why in the test."""
    socket.socket = _real_socket
    socket.create_connection = _real_create_connection
    yield
