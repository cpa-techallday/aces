"""Evidence ledger integrity (audit M3) and the hardened git call behind every record (audit H1).

Every record, ledger, key and repository here is a test input made up for the checks.
"""
import json
import os
import subprocess
import threading
import time

import pytest

from aces import evidence
from aces.cli import main

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX hooks and shell scripts")
KEY = "test-input-key-0123456789abcdef"


def _rec(spec_dict, i=0):
    return evidence.make_record(kind="t", spec=spec_dict, model_id="m", model_version="0", inputs={"i": i},
                                observations={}, calibration={})


def _ledger(tmp_path, spec_dict, n=3):
    led = evidence.Ledger(tmp_path / "ledger.jsonl")
    for i in range(n):
        led.append(_rec(spec_dict, i))
    return led


def _rewrite(led, mutate):
    """A full rewrite with every hash recomputed: what an attacker with write access can do (test input)."""
    recs = [json.loads(ln) for ln in led.path.read_text().splitlines()]
    mutate(recs)
    prev, lines = evidence.GENESIS, []
    for r in recs:
        r["prev_hash"] = prev
        r.pop("record_hash", None)
        r["record_hash"] = evidence.canonical_hash(r)
        prev = r["record_hash"]
        lines.append(json.dumps(r, sort_keys=True))
    led.path.write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------------------- anchoring the head

def test_a_full_rewrite_fails_against_the_kept_head(tmp_path, spec_dict, capsys):
    led = _ledger(tmp_path, spec_dict)
    n, head = led.head()
    _rewrite(led, lambda recs: recs[0]["inputs"].update(i=99))
    assert led.verify()[0]  # the chain alone can't see it
    ok, msg = led.verify(expect_head=head)
    assert not ok and "not in the chain" in msg
    assert main(["ledger", "verify", str(led.path), "--expect-head", head, "--min-records", str(n)]) == 1


def test_records_cut_off_the_end_fail_against_the_kept_count(tmp_path, spec_dict):
    led = _ledger(tmp_path, spec_dict)
    n, head = led.head()
    lines = led.path.read_text().splitlines(keepends=True)
    led.path.write_text("".join(lines[:-1]))
    assert led.verify()[0]  # the chain alone can't see it
    assert not led.verify(min_records=n)[0]
    assert not led.verify(expect_head=head)[0]
    assert main(["ledger", "verify", str(led.path), "--min-records", str(n)]) == 1


def test_head_command_prints_what_to_keep(tmp_path, spec_dict, capsys):
    led = _ledger(tmp_path, spec_dict, 2)
    assert main(["ledger", "head", str(led.path)]) == 0
    out = capsys.readouterr().out
    n, head = led.head()
    assert f"records {n}" in out and f"head {head}" in out
    led.append(_rec(spec_dict, 9))  # later appends keep the kept head valid
    ok, msg = led.verify(expect_head=head, min_records=n)
    assert ok and "1 record(s) appended since" in msg
    assert main(["ledger", "verify", str(led.path), "--expect-head", head[:12]]) == 2  # full hash only


def test_signed_head_catches_rewrite_and_truncation(tmp_path, spec_dict, monkeypatch):
    monkeypatch.setenv(evidence.HMAC_ENV, KEY)
    led = _ledger(tmp_path, spec_dict)
    assert led.head_path.exists() and KEY not in led.head_path.read_text()
    ok, msg = led.verify()
    assert ok and "signed head checked" in msg
    good = led.path.read_bytes()
    _rewrite(led, lambda recs: recs[1]["inputs"].update(i=99))
    ok, msg = led.verify()
    assert not ok and "signed head" in msg
    led.path.write_bytes(good[: good.rindex(b"\n", 0, len(good) - 1) + 1])  # last record cut off
    assert not led.verify()[0]
    led.path.write_bytes(good)
    assert led.verify()[0]
    with pytest.raises(evidence.LedgerError):  # an append is refused on a ledger that fails its head
        led.path.write_bytes(good[: good.rindex(b"\n", 0, len(good) - 1) + 1])
        led.append(_rec(spec_dict, 7))


def test_signed_head_needs_the_key_and_the_file(tmp_path, spec_dict, monkeypatch):
    led = _ledger(tmp_path, spec_dict, 2)  # written without a key: no head file
    monkeypatch.setenv(evidence.HMAC_ENV, KEY)
    ok, msg = led.verify()
    assert not ok and "missing" in msg and "--sign" in msg
    assert main(["ledger", "head", str(led.path), "--sign"]) == 0
    assert led.verify()[0]
    head = json.loads(led.head_path.read_text())
    head["records"] = 1  # forged to match a truncation
    led.head_path.write_text(json.dumps(head))
    ok, msg = led.verify()
    assert not ok and "does not match its signature" in msg
    monkeypatch.setenv(evidence.HMAC_ENV, "short")
    assert not led.verify()[0]


# ----------------------------------------------------------------------------- line format

def test_duplicate_key_fails(tmp_path, spec_dict):
    led = _ledger(tmp_path, spec_dict, 1)
    line = led.path.read_text().rstrip("\n")
    led.path.write_text(line[:-1] + ', "kind": "edited"}\n')  # JSON keeps the last value of a repeated key
    ok, msg = led.verify()
    assert not ok and "duplicate key 'kind'" in msg


@pytest.mark.parametrize("line", ["[1, 2]", "5", '"text"', "null"])
def test_a_line_that_is_not_an_object_fails_cleanly(tmp_path, spec_dict, line):
    led = _ledger(tmp_path, spec_dict, 1)
    with led.path.open("a") as fh:
        fh.write(line + "\n")
    ok, msg = led.verify()
    assert not ok and "record 1: not a JSON object" in msg
    assert main(["ledger", "verify", str(led.path)]) == 1


# ----------------------------------------------------------------------------- interrupted appends

def test_an_interrupted_append_is_kept_and_sealed(tmp_path, spec_dict, capsys):
    led = _ledger(tmp_path, spec_dict, 2)
    partial = b'{"company": "CPA", "kind": "t", "inp'
    with led.path.open("ab") as fh:
        fh.write(partial)
    ok, msg = led.verify()
    assert not ok and "interrupted" in msg
    with pytest.raises(evidence.LedgerError, match="aces ledger seal"):
        led.append(_rec(spec_dict, 5))
    assert main(["ledger", "seal", str(led.path)]) == 0
    raw = led.path.read_bytes()
    assert partial + b"\n" in raw  # the line is kept exactly as written (rule 10)
    ok, msg = led.verify()
    assert ok and "1 sealed interrupted line" in msg
    led.append(_rec(spec_dict, 6))  # appends continue the chain after the seal record
    ok, msg = led.verify()
    assert ok and msg.startswith("4 records")
    assert [r["kind"] for r in led.records()] == ["t", "t", evidence.SEAL_KIND, "t"]
    assert main(["ledger", "seal", str(led.path)]) == 1  # nothing left to seal


def test_seal_refuses_any_other_fault(tmp_path, spec_dict):
    led = _ledger(tmp_path, spec_dict, 2)
    lines = led.path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["inputs"]["i"] = 42
    lines[0] = json.dumps(rec, sort_keys=True)
    led.path.write_text("\n".join(lines) + "\n" + '{"partial')
    with pytest.raises(evidence.LedgerError, match="contents changed"):
        led.seal()


def test_a_seal_record_cannot_excuse_a_different_line(tmp_path, spec_dict):
    led = _ledger(tmp_path, spec_dict, 1)
    with led.path.open("ab") as fh:
        fh.write(b'{"partial')
    led.seal()
    data = led.path.read_bytes().replace(b'{"partial\n', b'{"swapped\n')
    led.path.write_bytes(data)
    ok, msg = led.verify()
    assert not ok and "not valid JSON" in msg


# ----------------------------------------------------------------------------- locking and fsync

def test_a_stale_lock_file_does_not_block(tmp_path, spec_dict):
    led = _ledger(tmp_path, spec_dict, 1)
    (tmp_path / "ledger.jsonl.lock").write_text("left by a crashed run (test input)")
    t0 = time.monotonic()
    led.append(_rec(spec_dict, 1))
    assert time.monotonic() - t0 < 5 and led.verify()[0]


def test_verify_waits_for_the_writer_lock(tmp_path, spec_dict):
    led = _ledger(tmp_path, spec_dict, 1)
    done = threading.Event()
    result = {}

    def verify():
        result["v"] = led.verify()
        done.set()

    with evidence._lock(led.path):
        t = threading.Thread(target=verify)
        t.start()
        assert not done.wait(0.5)  # blocked while a writer holds the lock
    t.join(10)
    assert done.is_set() and result["v"][0]


def test_concurrent_appends_keep_one_chain(tmp_path, spec_dict):
    led = evidence.Ledger(tmp_path / "ledger.jsonl")
    recs = [_rec(spec_dict, i) for i in range(12)]
    threads = [threading.Thread(target=lambda rs=recs[k::3]: [evidence.Ledger(led.path).append(r) for r in rs])
               for k in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    ok, msg = led.verify()
    assert ok and msg.startswith("12 records")


def test_appends_are_fsynced(tmp_path, spec_dict, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    _ledger(tmp_path, spec_dict, 1)
    assert synced


# ----------------------------------------------------------------------------- git describe (H1)

def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null"]
    (repo / ".gitattributes").write_text("* filter=evil\n")
    (repo / "a.txt").write_text("test input\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "test"]):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    return repo


@posix_only
@pytest.mark.parametrize("attack", ["fsmonitor", "filter", "hook"])
def test_code_revision_does_not_run_planted_git_config(tmp_path, attack):
    repo = _repo(tmp_path)
    marker = tmp_path / "PWNED"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    script.chmod(0o755)
    if attack == "fsmonitor":
        subprocess.run(["git", "config", "core.fsmonitor", str(script)], cwd=repo, check=True)
    elif attack == "filter":
        subprocess.run(["git", "config", "filter.evil.clean", str(script)], cwd=repo, check=True)
    else:
        hook = repo / ".git" / "hooks" / "post-index-change"
        hook.write_text(script.read_text())
        hook.chmod(0o755)
    time.sleep(1.1)
    (repo / "a.txt").write_text("test input\n")  # same content, newer mtime: git re-reads it through the filter
    rev = evidence.code_revision(repo)
    assert rev != "unversioned"
    assert not marker.exists()


def test_git_gets_the_minimal_environment(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))  # inherited, this would point git elsewhere
    assert evidence.code_revision(repo) != "unversioned"
