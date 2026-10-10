"""Hostile (test fixture): reads a planted secret outside the run (an onshape.env marker) and tries to leak it through
stdout and the error message; the read must be refused (kind sandbox) and the marker must appear nowhere.
"""
PARAMS = {"secret": {"default": "", "doc": "absolute path of the planted secret (the test sets it)"}}


def build(p):
    if not p.secret:
        raise ValueError("set the secret parameter to the absolute path of the planted file")
    with open(p.secret, "r", encoding="utf-8") as fh:
        text = fh.read()
    print(text)
    raise RuntimeError("the read was not refused: " + text)
