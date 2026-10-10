"""Hostile (test fixture): a TCP connection to a loopback port; the sandbox must refuse it (kind sandbox, exit 5).

The port comes from the params (the test listens on it and checks that nothing was accepted); the host is always the
loopback address, so this fixture can never reach another machine even when a sandbox layer is missing.
"""
import socket

PARAMS = {"port": {"default": "9", "doc": "loopback TCP port to connect to (the test sets it)"}}


def build(p):
    with socket.create_connection(("127.0.0.1", int(p.port)), timeout=2) as conn:
        conn.sendall(b"aces-cad hostile fixture")
    raise RuntimeError("the connection was not refused")
