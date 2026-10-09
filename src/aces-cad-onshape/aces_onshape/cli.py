"""The aces-onshape command: the Onshape adapter's verbs.

  aces-onshape [--timeout SECONDS] [--verbose] [--version] VERB ...
    whoami | documents [QUERY] | create-document NAME | elements DID WVM WVMID | fs read|write | eval | features
    add-feature | add-custom-feature | update-feature | delete-feature | version | massprops | export step|stl | pull

Every verb takes --json. Verbs that send content to Onshape, change a document or write local files take --project
(default: ACES_ONSHAPE_PROJECT) and are refused for sovereign and controlled projects. The CLI keeps no session, so
rewriting or deleting anything that already exists (fs write --eid, update-feature, delete-feature,
add-custom-feature --replace) needs --confirm-foreign, and writing into a public document needs --allow-public.

Exit codes: 0 ok; 1 an Onshape error, an internal error, or a result with ok false (a studio that did not compile, an
eval with an ERROR notice, a feature with status ERROR); 2 usage; 3 no or rejected API keys; 4 class refused;
130 interrupted. Output never contains the API keys: all of it passes through the redactor.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__, config, ops
from .client import STL_UNITS
from .errors import AdapterError, UsageError

PROG = "aces-onshape"


class _IO:
    """stdout and stderr through the redactor; it becomes the client's redactor once a client is built."""

    def __init__(self):
        self.redactor = config.redactor_from_env()

    def use(self, redactor) -> None:
        self.redactor = redactor

    def out(self, text: str) -> None:
        text = self.redactor.redact(text)
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.stdout.flush()

    def source(self, text: str) -> None:
        """FeatureScript source as UTF-8 bytes, unchanged (no newline translation on Windows)."""
        text = self.redactor.redact(text)
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(text)
            sys.stdout.flush()
            return
        sys.stdout.flush()
        buffer.write(text.encode("utf-8", "backslashreplace"))
        buffer.flush()

    def err(self, text: str) -> None:
        sys.stderr.write(self.redactor.redact(f"{PROG}: {text}") + "\n")
        sys.stderr.flush()

    def raw_err(self, text: str) -> None:
        sys.stderr.write(self.redactor.redact(text))
        sys.stderr.flush()


class _Parser(argparse.ArgumentParser):
    io: _IO | None = None

    def _print_message(self, message, file=None):
        if message and _Parser.io is not None:
            message = _Parser.io.redactor.redact(message)
        super()._print_message(message, file)


def _positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not number > 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog=PROG, description="ACES Onshape adapter: FeatureScript, features, exports and the CadSource pull "
                                       "over the Onshape REST API (a cloud service: refused for sovereign and "
                                       "controlled projects).")
    p.add_argument("--timeout", type=_positive, default=30.0, metavar="SECONDS",
                   help="per-request socket timeout (default 30)")
    p.add_argument("--verbose", action="store_true",
                   help="trace each HTTP exchange to stderr (never headers) and print tracebacks for internal errors")
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="verb", metavar="VERB")
    sub.required = True

    def leaf(parent, name: str, text: str):
        s = parent.add_parser(name, help=text, description=text)
        s.add_argument("--json", action="store_true", help="print the result as JSON")
        return s

    def project(s, *, foreign: bool = False, public: bool = False) -> None:
        s.add_argument("--project", metavar="NAME", default=None,
                       help="the ACES project this is for (default: ACES_ONSHAPE_PROJECT); sovereign and controlled "
                            "projects are refused")
        if foreign:
            s.add_argument("--confirm-foreign", action="store_true",
                           help="change a target this command did not create (only after checking it is yours, with a "
                                "version as a restore point)")
        if public:
            s.add_argument("--allow-public", action="store_true",
                           help="write into a public document (everything in it is published)")

    def ids(s, *names: str) -> None:
        for n in names:
            s.add_argument(n.lower(), metavar=n)

    leaf(sub, "whoami", "the account the API keys belong to (sessioninfo)")

    s = leaf(sub, "documents", "search the account's documents by name")
    s.add_argument("query", nargs="?", default="", metavar="QUERY")
    s.add_argument("--limit", type=int, default=20, metavar="N", help="at most this many (1-20, default 20)")

    s = leaf(sub, "create-document", "WRITES: create a new document (private when the plan allows)")
    s.add_argument("name", metavar="NAME")
    s.add_argument("--description", default=None, metavar="TEXT")
    project(s)

    s = leaf(sub, "elements", "the tabs (elements) of a workspace, version or microversion")
    ids(s, "DID", "WVM", "WVMID")
    s.add_argument("--type", dest="element_type", default=None, metavar="ELEMENT_TYPE",
                   help="only this element type (PARTSTUDIO, FEATURESTUDIO, ASSEMBLY, BLOB, ...)")

    fs = sub.add_parser("fs", help="read or write a Feature Studio's FeatureScript source")
    fss = fs.add_subparsers(dest="fs_verb", metavar="read|write")
    fss.required = True
    s = leaf(fss, "read", "print a Feature Studio's source")
    ids(s, "DID", "WVM", "WVMID", "EID")
    s.add_argument("--out", default=None, metavar="FILE", help="write the source to FILE (UTF-8, unchanged)")
    s = leaf(fss, "write", "WRITES: replace a Feature Studio's whole source, or create a studio (no --eid)")
    ids(s, "DID", "WID")
    s.add_argument("file", metavar="FILE|-", help="FeatureScript source (- reads stdin); omit the header to keep "
                                                  "the studio's own")
    s.add_argument("--eid", default=None, metavar="EID", help="the existing Feature Studio to replace")
    s.add_argument("--name", default=None, metavar="NAME", help="name of a new Feature Studio")
    project(s, foreign=True, public=True)

    s = leaf(sub, "eval", "evaluate a FeatureScript lambda against a Part Studio (creates nothing)")
    ids(s, "DID", "WVM", "WVMID", "EID")
    s.add_argument("file", metavar="FILE|-", help="function(context is Context, queries is map) { ... } (- reads stdin)")
    s.add_argument("--queries", default=None, metavar="JSON", help='{"name": ["deterministic id", ...]}')
    s.add_argument("--raw", action="store_true", help="also return Onshape's raw result value")
    project(s)

    s = leaf(sub, "features", "the features of a Part Studio")
    ids(s, "DID", "WVM", "WVMID", "EID")
    s.add_argument("--raw", action="store_true", help="also return Onshape's full feature list")

    s = leaf(sub, "add-feature", "WRITES: add one feature (a raw BTMFeature-134 JSON map) to a Part Studio")
    ids(s, "DID", "WID", "EID")
    s.add_argument("file", metavar="FILE|-", help="the BTMFeature-134 map as JSON (- reads stdin)")
    project(s, public=True)

    s = leaf(sub, "add-custom-feature", "WRITES: instantiate a custom feature from a Feature Studio in a Part Studio")
    ids(s, "DID", "WID", "EID", "STUDIO_EID", "FEATURE_TYPE")
    s.add_argument("--param", action="append", default=None, metavar="ID=VALUE",
                   help='a parameter; VALUE is JSON when it parses (true, 3, ["JHD"]), else a string ("20 mm")')
    s.add_argument("--params", default=None, metavar="JSON", help="parameters as a JSON object (--param wins)")
    s.add_argument("--name", default=None, metavar="NAME", help="feature name (default: the feature type name)")
    s.add_argument("--pin-version", action="store_true",
                   help="first create a permanent version and point the feature at it")
    s.add_argument("--replace", default=None, metavar="FID", help="replace this existing instance instead of adding")
    project(s, foreign=True, public=True)

    s = leaf(sub, "update-feature", "WRITES: replace one feature with a BTMFeature-134 JSON map")
    ids(s, "DID", "WID", "EID", "FID")
    s.add_argument("file", metavar="FILE|-", help="the BTMFeature-134 map as JSON (- reads stdin)")
    project(s, foreign=True, public=True)

    s = leaf(sub, "delete-feature", "WRITES: delete one feature (it stays in the document history)")
    ids(s, "DID", "WID", "EID", "FID")
    project(s, foreign=True, public=True)

    s = leaf(sub, "version", "WRITES: create a permanent version (a restore point; an immutable source to pull)")
    ids(s, "DID", "NAME")
    s.add_argument("--wid", default=None, metavar="WID", help="the workspace (default: the document's default)")
    s.add_argument("--description", default=None, metavar="TEXT")
    project(s, public=True)

    s = leaf(sub, "massprops", "mass properties of a Part Studio, as Onshape returns them")
    ids(s, "DID", "WVM", "WVMID", "EID")

    ex = sub.add_parser("export", help="export a Part Studio to ~/Work/<project>/cad/onshape/exports/ with a sidecar")
    exs = ex.add_subparsers(dest="export_verb", metavar="step|stl")
    exs.required = True
    s = leaf(exs, "step", "STEP AP242 through a translation job (not stored in the document)")
    ids(s, "DID", "WV", "WVID", "EID")
    s.add_argument("--label", default=None, metavar="LABEL", help="appended to the file name")
    project(s)
    s = leaf(exs, "stl", "binary STL")
    ids(s, "DID", "WVM", "WVMID", "EID")
    s.add_argument("--label", default=None, metavar="LABEL", help="appended to the file name")
    s.add_argument("--units", default="millimeter", choices=STL_UNITS, metavar="UNIT",
                   help=f"one of {', '.join(STL_UNITS)} (default millimeter)")
    project(s)

    s = leaf(sub, "pull", "the CadSource pull: part.step, parameters.json, source.json and a cad.pull ledger entry")
    s.add_argument("ref", metavar="REF", help="onshape://<did>/<wid>/<eid>, onshape://<did>/v/<vid>/<eid>, or an "
                                              "Onshape document URL on the configured host")
    project(s)
    return p


def _read_input(spec: str, what: str) -> str:
    try:
        if spec == "-":
            stream = getattr(sys.stdin, "buffer", None)
            data = stream.read() if stream is not None else sys.stdin.read().encode("utf-8")
        else:
            data = Path(spec).read_bytes()
    except OSError as e:
        raise UsageError(f"cannot read {what} {spec}: {e.strerror or type(e).__name__}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UsageError(f"{what} {spec} is not UTF-8 text") from None


def _json(text: str, what: str):
    try:
        return json.loads(text)
    except ValueError as e:
        raise UsageError(f"{what} is not valid JSON ({e})") from None


def _parameters(args) -> dict | None:
    values: dict = {}
    if args.params is not None:
        obj = _json(args.params, "--params")
        if not isinstance(obj, dict):
            raise UsageError("--params must be a JSON object mapping parameter ids to values")
        values.update(obj)
    for item in args.param or []:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--param needs ID=VALUE; got {item!r}")
        try:
            values[key] = json.loads(raw)
        except ValueError:
            values[key] = raw
    return values or None


def _operation(args) -> tuple[str, dict]:
    """The operation name and its keyword arguments; reads FILE|- inputs (local, no network)."""
    v = args.verb
    if v == "whoami":
        return "whoami", {}
    if v == "documents":
        return "documents", {"query": args.query, "limit": args.limit}
    if v == "create-document":
        return "create_document", {"name": args.name, "description": args.description, "project": args.project}
    if v == "elements":
        return "elements", {"did": args.did, "wvm": args.wvm, "wvmid": args.wvmid, "element_type": args.element_type}
    if v == "fs" and args.fs_verb == "read":
        return "featurestudio_read", {"did": args.did, "wvm": args.wvm, "wvmid": args.wvmid, "eid": args.eid}
    if v == "fs":
        return "featurestudio_write", {"did": args.did, "wid": args.wid, "code": _read_input(args.file, "FeatureScript"),
                                       "eid": args.eid, "name": args.name, "project": args.project,
                                       "confirm_foreign": args.confirm_foreign, "allow_public": args.allow_public}
    if v == "eval":
        queries = _json(args.queries, "--queries") if args.queries is not None else None
        return "featurescript_eval", {"did": args.did, "wvm": args.wvm, "wvmid": args.wvmid, "eid": args.eid,
                                      "script": _read_input(args.file, "script"), "queries": queries,
                                      "raw": args.raw, "project": args.project}
    if v == "features":
        return "features", {"did": args.did, "wvm": args.wvm, "wvmid": args.wvmid, "eid": args.eid, "raw": args.raw}
    if v == "add-feature":
        return "add_feature", {"did": args.did, "wid": args.wid, "eid": args.eid,
                               "feature": _json(_read_input(args.file, "feature"), "the feature"),
                               "project": args.project, "allow_public": args.allow_public}
    if v == "add-custom-feature":
        return "add_custom_feature", {"did": args.did, "wid": args.wid, "eid": args.eid, "studio_eid": args.studio_eid,
                                      "feature_type": args.feature_type, "parameters": _parameters(args),
                                      "name": args.name, "pin_version": args.pin_version,
                                      "replace_feature_id": args.replace, "project": args.project,
                                      "confirm_foreign": args.confirm_foreign, "allow_public": args.allow_public}
    if v == "update-feature":
        return "update_feature", {"did": args.did, "wid": args.wid, "eid": args.eid, "feature_id": args.fid,
                                  "feature": _json(_read_input(args.file, "feature"), "the feature"),
                                  "project": args.project, "confirm_foreign": args.confirm_foreign,
                                  "allow_public": args.allow_public}
    if v == "delete-feature":
        return "delete_feature", {"did": args.did, "wid": args.wid, "eid": args.eid, "feature_id": args.fid,
                                  "project": args.project, "confirm_foreign": args.confirm_foreign,
                                  "allow_public": args.allow_public}
    if v == "version":
        return "version_create", {"did": args.did, "name": args.name, "wid": args.wid,
                                  "description": args.description, "project": args.project,
                                  "allow_public": args.allow_public}
    if v == "massprops":
        return "massproperties", {"did": args.did, "wvm": args.wvm, "wvmid": args.wvmid, "eid": args.eid}
    if v == "export" and args.export_verb == "step":
        return "export_step", {"did": args.did, "wvm": args.wv, "wvmid": args.wvid, "eid": args.eid,
                               "label": args.label, "project": args.project}
    if v == "export":
        return "export_stl", {"did": args.did, "wvm": args.wvm, "wvmid": args.wvmid, "eid": args.eid,
                              "label": args.label, "units": args.units, "project": args.project}
    if v == "pull":
        return "pull", {"ref": args.ref, "project": args.project}
    raise UsageError(f"unknown verb {v}")


# --- human output -------------------------------------------------------------------------------------------------

def _yn(value) -> str:
    return "yes" if value else "no"


def _warnings(result: dict) -> list[str]:
    return [f"warning: {w}" for w in result.get("warnings") or []]


def _h_whoami(r: dict) -> list[str]:
    head = f"{r.get('name')} <{r.get('email')}>"
    if r.get("plan_group"):
        head += f"  plan {r['plan_group']}"
    if r.get("company"):
        head += f"  company {r['company']}"
    return [head, f"user {r.get('id')}; API {r.get('base_url')}; keys from {r.get('credentials_source')}"]


def _h_documents(r: dict) -> list[str]:
    docs = r.get("documents") or []
    if not docs:
        return [f"no documents match {r.get('query')!r}" if r.get("query") else "no documents"]
    lines = [f"{d.get('id')}  w {d.get('default_workspace_id')}  {d.get('modified_at')}  {d.get('name')}" for d in docs]
    if r.get("has_more"):
        lines.append("(more documents match: narrow the query)")
    return lines


def _element_rows(els: list) -> list[str]:
    if not els:
        return ["(no elements)"]
    return [f"{e.get('id')}  {str(e.get('element_type')):15s} m {e.get('microversion_id')}  {e.get('name')}"
            for e in els]


def _h_create_document(r: dict) -> list[str]:
    return ([f"created document {r.get('did')} (workspace {r.get('wid')}, public: {_yn(r.get('public'))}): "
             f"{r.get('name')}"] + _element_rows(r.get("elements") or []) + _warnings(r))


def _h_elements(r: dict) -> list[str]:
    return _element_rows(r.get("elements") or [])


def _h_fs_write(r: dict) -> list[str]:
    lines = [f"Feature Studio {r.get('eid')} {'created' if r.get('created') else 'updated'} in document "
             f"{r.get('did')} workspace {r.get('wid')} (microversion {r.get('microversion')}; header prepended: "
             f"{_yn(r.get('header_prepended'))})",
             f"restore point: previous microversion {r.get('previous_microversion')}, previous sha256 "
             f"{r.get('previous_contents_sha256')}"]
    specs = r.get("feature_specs") or []
    if specs:
        lines.append("feature types: " + ", ".join(f"{s.get('feature_type')} ({s.get('feature_type_name')})"
                                                   for s in specs))
    else:
        lines.append("feature types: none reported")
    if r.get("specs_error"):
        lines.append(f"specs error: {r['specs_error']}")
    for n in r.get("notices") or []:
        lines.append(f"notice {n.get('level')} {n.get('type')}: {n.get('message')}")
    return lines + _warnings(r)


def _h_eval(r: dict) -> list[str]:
    lines = [json.dumps(r.get("result"), indent=1, ensure_ascii=False)]
    if r.get("console"):
        lines.append("console:")
        lines.append(str(r["console"]).rstrip("\n"))
    for n in r.get("notices") or []:
        lines.append(f"notice {n.get('level')} {n.get('type')}: {n.get('message')}")
    return lines


def _h_features(r: dict) -> list[str]:
    lines = [f"{r.get('count')} features (microversion {r.get('microversion')})"]
    for f in r.get("features") or []:
        lines.append(f"{f.get('feature_id')}  {str(f.get('status') or '-'):8s} {f.get('feature_type')}  "
                     f"ns {f.get('namespace') or '-'}  {f.get('name')}")
    return lines


def _h_feature(r: dict) -> list[str]:
    lines = [f"feature {r.get('feature_id')} {r.get('name')!r} ({r.get('feature_type')}): {r.get('feature_status')}"
             + (" (replaced)" if r.get("replaced") else "")]
    messages = r.get("messages")
    if isinstance(messages, dict):
        for key in ("error", "warning", "info"):
            if messages.get(key):
                lines.append(f"{key}: {messages[key]}")
    if "namespace" in r:
        lines.append(f"namespace {r.get('namespace')} ({r.get('namespace_source')}, {r.get('namespace_form')} form)"
                     + (f"; version {r.get('version_id')}" if r.get("version_id") else ""))
    return lines + _warnings(r)


def _h_delete(r: dict) -> list[str]:
    line = f"deleted feature {r.get('deleted')} (microversion {r.get('microversion')})"
    if r.get("already_deleted_on_retry"):
        line = f"feature {r.get('deleted')} is deleted: the first attempt was applied before a retry"
    return [line]


def _h_version(r: dict) -> list[str]:
    return [f"version {r.get('version_id')} {r.get('name')!r} of document {r.get('did')} workspace {r.get('wid')} "
            f"(microversion {r.get('microversion')}, created {r.get('created_at')})"]


def _h_massprops(r: dict) -> list[str]:
    return [json.dumps(r.get("bodies"), indent=1, ensure_ascii=False), f"({r.get('note')})"]


def _h_export(r: dict) -> list[str]:
    lines = [f"wrote {r.get('path')} ({r.get('bytes')} bytes, sha256 {r.get('sha256')})",
             f"sidecar {r.get('sidecar')}"]
    if r.get("document_public"):
        lines.append("note: the document is public")
    return lines


def _h_pull(r: dict) -> list[str]:
    lines = [f"bundle {r.get('bundle')}"]
    for name, info in (r.get("files") or {}).items():
        lines.append(f"  {name:16s} {str(info.get('bytes')):>10} bytes  sha256 {info.get('sha256')}")
    ledger = r.get("ledger") or {}
    if ledger.get("written"):
        lines.append(f"ledger: cad.pull written (entry {ledger.get('n')}, hash {str(ledger.get('hash'))[:12]})")
    else:
        lines.append(f"ledger: not written: {ledger.get('reason')}")
    cad = r.get("cad_source") or {}
    lines.append(f"cad source: recorded {cad.get('ref')}" if cad.get("recorded")
                 else f"cad source: not recorded: {cad.get('reason')}")
    lines.append("feature studios: " + ("all captured" if r.get("feature_studios_complete")
                                        else "INCOMPLETE capture (see parameters.json)"))
    return lines + _warnings(r)


HUMAN = {
    "whoami": _h_whoami, "documents": _h_documents, "create_document": _h_create_document,
    "elements": _h_elements, "featurestudio_write": _h_fs_write, "featurescript_eval": _h_eval,
    "features": _h_features, "add_feature": _h_feature, "add_custom_feature": _h_feature,
    "update_feature": _h_feature, "delete_feature": _h_delete, "version_create": _h_version,
    "massproperties": _h_massprops, "export_step": _h_export, "export_stl": _h_export, "pull": _h_pull,
}


def _show(io: _IO, args, op: str, result: dict) -> None:
    if op == "featurestudio_read" and args.out:
        data = (result.get("contents") or "").encode("utf-8")
        try:
            Path(args.out).write_bytes(data)
        except OSError as e:
            raise UsageError(f"cannot write {args.out}: {e.strerror or type(e).__name__}") from None
        if not args.json:
            io.out(f"wrote {args.out} ({len(data)} bytes, sha256 {result.get('contents_sha256')})")
            return
    if args.json:
        io.out(json.dumps(result, indent=1, ensure_ascii=False))
    elif op == "featurestudio_read":
        io.source(result.get("contents") or "")
    else:
        io.out("\n".join(HUMAN[op](result)))


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass
    io = _IO()
    _Parser.io = io
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    trace = (lambda line: io.err(line)) if args.verbose else None
    try:
        op, kwargs = _operation(args)
        result = ops.dispatch(op, kwargs, session=ops.Session(), timeout=args.timeout, trace=trace,
                              on_client=lambda client: io.use(client.redactor))
        _show(io, args, op, result)
        return 1 if isinstance(result, dict) and result.get("ok") is False else 0
    except KeyboardInterrupt:
        io.err("interrupted")
        return 130
    except AdapterError as e:
        io.err(str(e))
        return e.exit_code
    except Exception as e:  # noqa: BLE001 (no traceback without --verbose)
        io.err(f"internal error: {type(e).__name__}: {e}")
        if args.verbose:
            io.raw_err(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
