"""Safe output writes and the process-tree runner (audit M1, C1, C4, H1, I3/C5).  All inputs are test inputs."""
import os
import subprocess
import sys
import time

import pytest

from aces import proc, safeio

posix_only = pytest.mark.skipif(os.name != "posix", reason="symlink semantics checked on POSIX")


def test_write_is_exclusive_unless_overwrite_is_asked_for(tmp_path):
    d = safeio.OutDir(tmp_path / "run")
    d.write_text("a.json", "1")
    with pytest.raises(FileExistsError):
        d.write_text("a.json", "2")
    d.write_text("a.json", "3", overwrite=True)
    assert (tmp_path / "run" / "a.json").read_text() == "3"
    assert [p.name for p in (tmp_path / "run").iterdir()] == ["a.json"]  # no temp file left behind


def test_new_folder_must_not_exist(tmp_path):
    safeio.OutDir(tmp_path / "run", new=True).close()
    with pytest.raises(FileExistsError):
        safeio.OutDir(tmp_path / "run", new=True)


@posix_only
def test_a_planted_file_link_is_refused_not_written_through(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    run = tmp_path / "run"
    run.mkdir()
    (run / "formgen.log").symlink_to(victim)
    d = safeio.OutDir(run)
    with pytest.raises(safeio.UnsafePathError):
        d.write_text("formgen.log", "x")
    with pytest.raises(safeio.UnsafePathError):
        d.open_append("formgen.log")
    d.write_text("formgen.log", "x", overwrite=True)  # replaces the link itself
    assert victim.read_text() == "keep"
    assert not (run / "formgen.log").is_symlink()


@posix_only
def test_swapping_the_run_folder_for_a_link_mid_run_does_not_redirect_writes(tmp_path):
    run, outside = tmp_path / "run", tmp_path / "outside"
    outside.mkdir()
    d = safeio.OutDir(run)
    d.write_text("first.json", "1")
    os.rename(run, tmp_path / "moved")
    run.symlink_to(outside)
    d.write_text("second.json", "2")
    assert not (outside / "second.json").exists()
    assert (tmp_path / "moved" / "second.json").read_text() == "2"


@posix_only
def test_a_linked_output_folder_is_refused(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(safeio.UnsafePathError):
        safeio.OutDir(tmp_path / "link")


def test_names_cannot_leave_the_folder(tmp_path):
    d = safeio.OutDir(tmp_path / "run")
    for bad in ("../x", "a/b", "..", ""):
        with pytest.raises(safeio.UnsafePathError):
            d.write_text(bad, "x")


def test_read_text_limited_refuses_large_files(tmp_path):
    p = tmp_path / "big.jsonl"
    p.write_text("x" * 100)
    assert safeio.read_text_limited(p, 100) == "x" * 100
    with pytest.raises(ValueError, match="limit"):
        safeio.read_text_limited(p, 99)


def test_proc_refuses_shell_strings():
    with pytest.raises(TypeError):
        proc.run("echo hi")


def test_proc_minimal_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    env = proc.minimal_env({"ARC1_PARAMS": "p.json"})
    assert "GH_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["ARC1_PARAMS"] == "p.json" and "PATH" in env
    out = proc.run([sys.executable, "-c", "import os; print(os.environ.get('GH_TOKEN'))"], env=env)
    assert out.stdout.strip() == "None"


@pytest.mark.skipif(os.name != "posix", reason="process-group check uses POSIX signals")
def test_proc_timeout_kills_the_grandchild_too(tmp_path):
    marker = tmp_path / "still_running"
    child = (f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', "
             f"'import time, pathlib; time.sleep(1.5); pathlib.Path(r\"{marker}\").write_text(\"x\")']); "
             f"import time; time.sleep(30)")
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run([sys.executable, "-c", child], timeout=0.5)
    assert time.monotonic() - t0 < 10
    time.sleep(2.0)
    assert not marker.exists()  # the grandchild was killed with the group


@pytest.mark.skipif(os.name != "posix", reason="process-group check uses POSIX signals")
def test_proc_timeout_kills_background_children_after_the_child_exits(tmp_path):
    """Verification finding: `sleep & exit 0` left the group running and the call waited for it."""
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run(["sh", "-c", "sleep 30 & exit 0"], timeout=1.0)
    assert time.monotonic() - t0 < 10


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_read_text_limited_refuses_a_fifo(tmp_path):
    """Verification finding: a planted FIFO blocked MCP reads forever."""
    f = tmp_path / "report.md"
    os.mkfifo(f)
    with pytest.raises(safeio.UnsafePathError):
        safeio.read_text_limited(f, 1000)
    with pytest.raises(safeio.UnsafePathError):
        safeio.sha256_of(f)


def test_outdir_sha256_reads_through_the_handle(tmp_path):
    import hashlib

    d = safeio.OutDir(tmp_path / "run")
    d.write_text("a.json", "[1]")
    assert d.sha256("a.json") == hashlib.sha256(b"[1]").hexdigest() == safeio.sha256_of(tmp_path / "run" / "a.json")
