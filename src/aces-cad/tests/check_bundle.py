"""Check an aces-cad pull bundle against its own records (build spec sections 15 and 21). Not a test module.

    python3 check_bundle.py PULL_JSON      (the output of aces-cad pull --json, or - for stdin)
    python3 check_bundle.py BUNDLE_DIR     (a bundle directory under <project>/cad/local/)

It recomputes the sha256 and size of every file source.json lists, checks geometry_sha256 against part.step (part.stl
for a mesh pull), checks the sha256 of source.json recorded in outcome.json, and, given a pull JSON, that the pull's
own bundle, files and geometry_sha256 agree with source.json. Standard library only. Exit 0 when everything matches,
1 on any mismatch, 2 when the input cannot be read.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath


def sha256_of(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _inside(bundle: Path, rel: str) -> Path | None:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts or "\\" in rel:
        return None
    return bundle.joinpath(*p.parts)


def _digest(entry) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
        return entry["sha256"]
    return None


def check(bundle: Path, pull: dict | None) -> list[str]:
    problems: list[str] = []
    out: list[str] = []
    source_path = bundle / "source.json"
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"cannot read {source_path}: {type(e).__name__}: {e}"]
    files = source.get("files")
    if not isinstance(files, dict) or not files:
        return [f"{source_path} lists no files"]
    for rel, entry in sorted(files.items()):
        want = _digest(entry)
        path = _inside(bundle, rel)
        if path is None:
            problems.append(f"{rel}: not a path inside the bundle")
            continue
        if not path.is_file() or path.is_symlink():
            problems.append(f"{rel}: missing (or not a regular file)")
            continue
        got, size = sha256_of(path)
        if want is None:
            problems.append(f"{rel}: source.json records no sha256")
        elif got != want:
            problems.append(f"{rel}: sha256 {got} but source.json says {want}")
        elif isinstance(entry, dict) and isinstance(entry.get("bytes"), int) and entry["bytes"] != size:
            problems.append(f"{rel}: {size} bytes but source.json says {entry['bytes']}")
        else:
            out.append(f"ok   {rel}  {got[:12]}  {size} bytes")
    kind = source.get("kind")
    geometry_file = "part.stl" if kind == "mesh" else "part.step"
    geo = bundle / geometry_file
    if geo.is_file():
        got, _size = sha256_of(geo)
        if got != source.get("geometry_sha256"):
            problems.append(f"geometry_sha256 {source.get('geometry_sha256')} is not the sha256 of {geometry_file} "
                            f"({got})")
        else:
            out.append(f"ok   geometry_sha256 = sha256({geometry_file})  {got[:12]}")
    else:
        problems.append(f"{geometry_file} is missing (kind {kind})")
    try:
        outcome = json.loads((bundle / "outcome.json").read_text(encoding="utf-8"))
        recorded = _digest((outcome.get("files") or {}).get("source.json"))
        got, _size = sha256_of(source_path)
        if recorded != got:
            problems.append(f"outcome.json records sha256 {recorded} for source.json, which is {got}")
        else:
            out.append(f"ok   outcome.json holds the sha256 of source.json  {got[:12]}")
    except (OSError, ValueError, AttributeError) as e:
        problems.append(f"cannot read outcome.json: {type(e).__name__}: {e}")
    leftovers = [p.name for p in bundle.parent.iterdir() if p.name.endswith(".partial")] if bundle.parent.is_dir() \
        else []
    if leftovers:
        problems.append(f"partial bundles left next to this one: {', '.join(sorted(leftovers))}")
    if pull is not None:
        if pull.get("geometry_sha256") != source.get("geometry_sha256"):
            problems.append(f"the pull result says geometry_sha256 {pull.get('geometry_sha256')}, source.json "
                            f"{source.get('geometry_sha256')}")
        for rel, entry in sorted((pull.get("files") or {}).items()):
            want = _digest(entry)
            if want is not None and rel in files and want != _digest(files[rel]):
                problems.append(f"the pull result and source.json disagree on {rel}")
    for line in out:
        print(line)
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 check_bundle.py PULL_JSON|-|BUNDLE_DIR", file=sys.stderr)
        return 2
    arg = argv[0]
    pull = None
    try:
        if arg != "-" and Path(arg).is_dir():
            bundle = Path(arg)
        else:
            text = sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")
            pull = json.loads(text)
            if not isinstance(pull, dict) or not isinstance(pull.get("bundle"), str):
                print("check_bundle: the pull JSON has no bundle (was the pull an error?)", file=sys.stderr)
                return 2
            bundle = Path(pull["bundle"])
    except (OSError, ValueError) as e:
        print(f"check_bundle: cannot read {arg}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if not bundle.is_dir():
        print(f"check_bundle: {bundle} is not a directory", file=sys.stderr)
        return 2
    problems = check(bundle, pull)
    for p in problems:
        print(f"BAD  {p}")
    print(f"check_bundle: {bundle}: {'all files match' if not problems else f'{len(problems)} mismatch(es)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
