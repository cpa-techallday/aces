"""A stand-in aces-cad worker for the tests, run through ACES_CAD_WORKER_CMD (build spec section 21).

It speaks the worker side of the job protocol (section 5.4): it reads the job JSON from stdin (at most 1 MiB) and
writes <job_dir>/result.json atomically (a temporary file in the same directory, then os.replace). It never imports
build123d and runs no agent code; every payload it writes is fixture output.

The mode is the first argument (the parent passes a worker only the allowlisted environment of section 8.1, so the
tests put it in the command); FAKE_WORKER_MODE is read only when no argument is given (a caller running the fake by
hand). Modes:

  ok                     an ok result whose payload reports the fake's environment, argv, cwd, pid, ppid and pgid
  error KIND [MESSAGE]   an error result of that kind (sandbox_unavailable, sandbox, memory, script, ...)
  no_result              exit 0 without a result.json
  bad_json               a result.json that is not JSON
  symlink_result         result.json is a symlink to a valid ok result outside the job directory's top level
  hardlink_out           out/parts.json with a second hard link (st_nlink 2), and an ok result that lists it
  oversize_result        a 5 MiB result.json
  segv                   dies with SIGSEGV (an access violation on Windows)
  grandchild_sleep       starts a grandchild that sleeps 600 s, prints "GRANDCHILD <pid>", then sleeps itself
  print_flood [BYTES [PAUSE_S]]
                         writes BYTES (default 10,000,000) to stdout and 1/10 of that to stderr, then an ok result;
                         with PAUSE_S it sleeps that long after every MiB, so the flood outlasts the parent's checks
  slow [SECONDS]         sleeps SECONDS (default 600), then an ok result
  noise                  ANSI escapes, control characters, Fontconfig lines and invalid UTF-8 on both streams, then ok
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

FIXTURE = "fake worker (fixture)"


def sandbox_block(job: dict) -> dict:
    limits = job.get("limits") or {}
    return {"mode": (job.get("sandbox") or {}).get("mode", "permissive"), "confined": False, "platform": FIXTURE,
            "kernel": FIXTURE, "seccomp": {"active": False, "detail": FIXTURE},
            "landlock": {"active": False, "abi": 0, "tcp_rules": False, "scoped": False, "skipped_roots": []},
            "audit_hook": False, "pdeathsig": False, "limits": limits,
            "limits_enforced": {"memory": False, "cpu": False, "file_size": False}, "missing": ["seccomp", "landlock"],
            "note": FIXTURE}


def result(job: dict, *, status: str = "ok", payload: dict | None = None, error: dict | None = None,
           files: list[str] | None = None) -> dict:
    return {"schema": "aces.cad.result/1", "job_id": job["job_id"], "op": job["op"], "status": status,
            "payload": payload if status == "ok" else None, "error": error, "files": files or [],
            "timings_s": {"prelude": 0.0, "guards": 0.0, "imports": 0.0, "op": 0.0}, "sandbox": sandbox_block(job),
            "versions": {"fake_worker": "0 (fixture)"}, "warnings": [FIXTURE]}


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def write_result(job_dir: Path, doc: dict) -> None:
    write_atomic(job_dir / "result.json", json.dumps(doc).encode("utf-8"))


def ok_payload(job: dict) -> dict:
    payload = {"fake": True, "env": dict(os.environ), "argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
               "ppid": os.getppid(), "job_keys": sorted(job), "op": job["op"]}
    if hasattr(os, "getpgid"):
        payload["pgid"] = os.getpgid(0)
        payload["sid"] = os.getsid(0)
    return payload


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else os.environ.get("FAKE_WORKER_MODE", "ok")
    extra = argv[1:]
    raw = sys.stdin.buffer.read((1 << 20) + 1)
    job = json.loads(raw.decode("utf-8"))
    job_dir = Path(job["job_dir"])
    print(f"FAKE_WORKER pid {os.getpid()} mode {mode}", flush=True)

    if mode == "ok":
        write_result(job_dir, result(job, payload=ok_payload(job)))
    elif mode == "error":
        kind = extra[0] if extra else "script"
        message = extra[1] if len(extra) > 1 else f"error of kind {kind} ({FIXTURE})"
        write_result(job_dir, result(job, status="error",
                                     error={"kind": kind, "message": message, "exception": "FakeError",
                                            "frames": [], "hint": None, "raised_in": None}))
    elif mode == "no_result":
        pass
    elif mode == "bad_json":
        write_atomic(job_dir / "result.json", b'{"schema": "aces.cad.result/1", not json')
    elif mode == "symlink_result":
        real = job_dir / "cwd" / "real-result.json"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(json.dumps(result(job, payload={"followed": True, "fake": True})), encoding="utf-8")
        try:
            os.symlink(real, job_dir / "result.json")
        except OSError as e:
            print(f"symlink_result: cannot create a symlink here: {e}", file=sys.stderr, flush=True)
            return 3
    elif mode == "hardlink_out":
        out = job_dir / "out"
        out.mkdir(parents=True, exist_ok=True)
        parts = {"schema": "aces.cad.parts/1", "kind": "solid", "datums": [],
                 "parts": [{"index": 0, "name": "fixture", "kind": "solid", "file": "p0.brep"}]}
        (out / "parts.json").write_text(json.dumps(parts), encoding="utf-8")
        (out / "p0.brep").write_text("not a brep (fixture)\n", encoding="utf-8")
        os.link(out / "parts.json", job_dir / "cwd" / "second-link.json")
        write_result(job_dir, result(job, payload=ok_payload(job), files=["parts.json", "p0.brep"]))
    elif mode == "oversize_result":
        doc = result(job, payload={"padding": "x" * (5 << 20)})
        write_atomic(job_dir / "result.json", json.dumps(doc).encode("utf-8"))
    elif mode == "segv":
        import faulthandler
        sys.stdout.flush()
        faulthandler.disable()
        faulthandler._sigsegv()
    elif mode == "grandchild_sleep":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        print(f"GRANDCHILD {child.pid}", flush=True)
        time.sleep(600)
        write_result(job_dir, result(job, payload=ok_payload(job)))
    elif mode == "print_flood":
        total = int(extra[0]) if extra else 10_000_000
        pause = float(extra[1]) if len(extra) > 1 else 0.0
        line = b"y" * 999 + b"\n"
        written = 0
        while written < total:
            sys.stdout.buffer.write(line)
            if written % 10_000 == 0:
                sys.stderr.buffer.write(line)
            written += len(line)
            if pause and written % (1 << 20) < len(line):  # a flood that keeps going: pause after every MiB
                sys.stdout.flush()
                time.sleep(pause)
        sys.stdout.flush()
        sys.stderr.flush()
        write_result(job_dir, result(job, payload=ok_payload(job)))
    elif mode == "slow":
        time.sleep(float(extra[0]) if extra else 600.0)
        write_result(job_dir, result(job, payload=ok_payload(job)))
    elif mode == "noise":
        sys.stdout.flush()
        os.write(1, b"\x1b[1;31mred stdout (fixture)\x1b[0m\n\x07bell\x08 and \x1b[?25lcursor (fixture)\n"
                    b"Fontconfig error: Cannot load default config file (fixture)\n"
                    b"bad utf-8 \xed\xa0\x80 \xff (fixture)\nplain stdout line (fixture)\n")
        os.write(2, b"Fontconfig warning: invalid constant used (fixture)\n\x1b[33mamber stderr (fixture)\x1b[0m\n"
                    b"tab\tkept (fixture)\n")
        write_result(job_dir, result(job, payload=ok_payload(job)))
    else:
        print(f"fake_worker: unknown mode {mode}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
