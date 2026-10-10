"""Hostile (test fixture): an HTTP request to a loopback port through urllib; refused by the sandbox (kind sandbox).

The URL is built from the port parameter, never from INPUTS, and always names the loopback address.
"""
import urllib.request

PARAMS = {"port": {"default": "9", "doc": "loopback TCP port to request (the test sets it)"}}


def build(p):
    with urllib.request.urlopen("http://127.0.0.1:" + p.port + "/aces-cad-hostile-fixture", timeout=2) as reply:
        reply.read()
    raise RuntimeError("the request was not refused")
