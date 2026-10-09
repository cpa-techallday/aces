# aces-cad-onshape

The Onshape adapter for ACES OS: a standard-library client for the Onshape REST API, the `aces-onshape` CLI, the
`aces-onshape-mcp` stdio MCP server and the `aces-cad-onshape` agent skill. An agent turns a request in words into
FeatureScript, writes it into a Feature Studio, instantiates the feature in a Part Studio, reads the errors and the
geometry back, iterates, and pulls the result into the project as STEP + parameters + sha256: the `onshape://` pull of
VISION.md section 5 step 2 and the pull half of the `CadSource` contract. Onshape is a cloud service, so every command
that sends content to Onshape, changes a document or writes local files runs the project class gate and refuses
`sovereign` and `controlled` projects before any request is sent.

Licence: MIT (`LICENSE`, Carbon Project Australia). This is the "ACES REST client" option of VISION.md's licence row,
written from Onshape's public OpenAPI spec with no third-party code; it is not `altendky/onshape-mcp` and not the
unlicensed `hedless` server. Python 3.11+ standard library only (no `requests`, no `mcp` package).

## Keys

Create API keys in the Onshape developer portal (`https://cad.onshape.com/appstore/dev-portal`). They authenticate
with HTTP Basic (the key as username, the secret as password), as Onshape documents for API keys, and every call made
with them counts against the account's annual API limits. Put them in `~/.config/aces/onshape.env`
(`$ACES_USER_CONFIG/onshape.env`), mode 0600:

```bash
install -Dm600 /dev/null ~/.config/aces/onshape.env
```

then add two lines with an editor:

```
ONSHAPE_ACCESS_KEY=<access key>
ONSHAPE_SECRET_KEY=<secret key>
```

**Precedence.** The keys and their base URL always come from the same source:

1. Both `ONSHAPE_ACCESS_KEY` and `ONSHAPE_SECRET_KEY` are set in the environment: the keys come from the environment,
   the base URL is the environment's `ONSHAPE_BASE_URL` or the default, and **the file is not read**.
2. Exactly one of them is set: refused (exit 3) with a message naming the one that is missing: set both, or unset both
   and use the file.
3. Neither is set: the file is read.
   - Both keys in the file: the base URL is the environment's `ONSHAPE_BASE_URL`, else the file's, else the default.
   - One key in the file: refused, naming the missing one.
   - No keys, or no file: refused with `no Onshape API keys: set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY, or write
     them as KEY=value lines to <path> (chmod 600). Create keys in the Onshape developer portal:
     https://cad.onshape.com/appstore/dev-portal` (one line, with the resolved path).

**The file:**

- One `KEY=value` per line. Blank lines and lines starting with `#` are skipped, a leading `export ` is ignored, one
  pair of matching surrounding quotes (`'` or `"`) is removed, and a UTF-8 byte-order mark (Notepad) is harmless.
- **No inline comments.** `ONSHAPE_SECRET_KEY=abc # note` makes the value `abc # note`, which is refused: a key must be
  printable ASCII without spaces, 1 to 512 characters. The refusal names the variable and its source, never the value.
- An empty value counts as unset, in the file and in the environment.
- A non-blank line without `=` is refused with its line number; the line itself is not shown.
- Recognised keys: `ONSHAPE_ACCESS_KEY`, `ONSHAPE_SECRET_KEY`, `ONSHAPE_BASE_URL`. Others are ignored.
- On Linux (any POSIX system) the file must carry no permission bits for group or others (mode 0600 or stricter) and
  must belong to you; otherwise it is refused, with the `chmod 600` command to run. Windows skips both checks.

**Base URL.** The default is `https://cad.onshape.com/api/v17`, the OpenAPI's server. `ONSHAPE_BASE_URL` points the
adapter at an enterprise domain: it must look like `https://<host>/api/v<N>`, https only, with no user info, query or
fragment. `http://` is accepted only for a loopback host when `ACES_ONSHAPE_ALLOW_HTTP_LOOPBACK=1` is in the
environment (never from the file); that exists for the tests' fake server.

**Secrets stay out of everything.** Keys are never printed, logged or written to any output, evidence or ledger
payload. CLI output, MCP results and the JSON files of a pull bundle pass through a redactor that replaces the access
key, the secret and the base64 credential with `[redacted]`; a bundle file that needed it carries a warning. The `aces`
subprocesses the adapter runs (`project show`, `ledger record`, `project add-cad-source`) get the environment without
the two key variables. Error messages name the call (`GET /documents/<did> -> HTTP 404: ...`), never a header, and a
redirect target is shown without its query, which can hold a pre-signed signature.

**Other settings** (environment only):

| Variable | Default | Meaning |
|---|---|---|
| `ACES_USER_CONFIG` | `~/.config/aces` | where `onshape.env` lives |
| `ACES_WORK` | `~/Work` | the projects: `<ACES_WORK>/<name>/aces.yaml`; bundles and exports go under `<ACES_WORK>/<name>/cad/onshape/` |
| `ACES_ONSHAPE_PROJECT` | unset | binds the CLI and the MCP server to one project (see the class gate) |
| `ACES_ONSHAPE_ACES_CMD` | unset: `aces` on `PATH` | the `aces` CLI as a JSON array of strings or a single path; `[]` means no aces CLI |
| `ACES_ONSHAPE_POLL_S` | `1.0` | the first STEP-translation poll interval (at least 0.01), then x1.5 up to 10 s |
| `ACES_ONSHAPE_TRANSLATION_TIMEOUT_S` | `600` | the total wait for a STEP translation |
| `ACES_ONSHAPE_ALLOW_HTTP_LOOPBACK` | unset | `1` permits `http://` to `127.0.0.1`, `::1` or `localhost` (the tests' fake server) |

## Commands

```
aces-onshape [--timeout SECONDS] [--verbose] [--version] VERB ...
```

| Command | What it does | Writes in Onshape |
|---|---|---|
| `whoami` | the account the keys belong to (sessioninfo), the base URL and where the keys came from | nothing |
| `documents [QUERY] [--limit N]` | searches the account's documents by name (at most 20) | nothing |
| `create-document NAME [--description TEXT] [--project NAME]` | a new document with a Part Studio; asks for a private one | a document |
| `elements DID WVM WVMID [--type ELEMENT_TYPE]` | the tabs of a workspace, version or microversion | nothing |
| `fs read DID WVM WVMID EID [--out FILE]` | a Feature Studio's source, to stdout or to FILE byte for byte | nothing |
| `fs write DID WID FILE\|- [--eid EID] [--name NAME] [--project NAME] [--confirm-foreign] [--allow-public]` | replaces a studio's whole source, or creates a studio when `--eid` is omitted; code without a `FeatureScript N;` header gets the studio's own header | a studio, or its whole source |
| `eval DID WVM WVMID EID FILE\|- [--queries JSON] [--raw] [--project NAME]` | evaluates a `function(context is Context, queries is map) { ... }` lambda against a Part Studio | nothing |
| `features DID WVM WVMID EID [--raw]` | the feature list with type, namespace, status and parameters | nothing |
| `add-feature DID WID EID FILE\|- [--project NAME] [--allow-public]` | adds one raw `BTMFeature-134` map before the rollback bar | one feature |
| `add-custom-feature DID WID EID STUDIO_EID FEATURE_TYPE [--param ID=VALUE]... [--params JSON] [--name NAME] [--pin-version] [--replace FID] [--project NAME] [--confirm-foreign] [--allow-public]` | instantiates a studio's feature; `--replace` re-points an existing instance, `--pin-version` first creates a version and points the feature at it | one feature or a replacement; with `--pin-version` also a permanent version |
| `update-feature DID WID EID FID FILE\|- [--project NAME] [--confirm-foreign] [--allow-public]` | replaces one feature with a `BTMFeature-134` map | replaces one feature |
| `delete-feature DID WID EID FID [--project NAME] [--confirm-foreign] [--allow-public]` | deletes one feature (it stays in the document history) | deletes one feature |
| `version DID NAME [--wid WID] [--description TEXT] [--project NAME] [--allow-public]` | a version of the workspace (default: the document's default workspace) | a permanent version |
| `massprops DID WVM WVMID EID` | mass properties as Onshape returns them (the schema states no units; mass needs a material) | nothing |
| `export step DID WV WVID EID [--project NAME] [--label LABEL]` | STEP AP242 through a translation job that is not stored in the document, with a sha256 sidecar | nothing |
| `export stl DID WVM WVMID EID [--project NAME] [--label LABEL] [--units UNIT]` | binary STL in millimeter (default), centimeter, meter, inch, foot or yard, with a sha256 sidecar | nothing |
| `pull REF [--project NAME]` | the CadSource pull (below) | nothing |

- Ids are Onshape's 24-character hex ids. `WVM` is `w` (workspace), `v` (version) or `m` (microversion); STEP and pull
  take only `w` or `v`.
- `REF` is `onshape://<did>/<wid>/<eid>` (a workspace), `onshape://<did>/v/<vid>/<eid>` (a version; `w` is accepted
  too), or a document URL `https://<host>/documents/<did>/w/<wid>/e/<eid>` on the configured host. A URL on any other
  host is refused: the adapter never sends a request to a host named in a reference.
- `--param ID=VALUE` takes VALUE as JSON when it parses (`true`, `3`, `["JHD"]`) and as a string otherwise
  (`--param length="20 mm"`); `--param` values merge over `--params`. Quantities are expressions with units.
- `FILE|-` reads the file or stdin as UTF-8 (a byte-order mark is dropped). `--json` prints the result as JSON.
- `--project` defaults to `ACES_ONSHAPE_PROJECT`. `--timeout` is the per-request socket timeout (default 30 s).
- `--verbose` traces each HTTP exchange to stderr (`aces-onshape: GET /users/sessioninfo -> 200 (<ms> ms)`), never a
  header or a pre-signed query, and prints a traceback for internal errors.
- **The CLI keeps no session.** Every existing studio or feature is foreign to it, so `fs write --eid`,
  `update-feature`, `delete-feature` and `add-custom-feature --replace` refuse (exit 2) without `--confirm-foreign`.
  Create a version first (`aces-onshape version`) so there is a restore point.
- Errors go to stderr as `aces-onshape: <message>`.

| Exit | Meaning |
|---|---|
| 0 | ok |
| 1 | an Onshape error (including a failed STEP translation and transport errors), an internal error, or an `ok: false` result: `fs write` with a featurespecs error or an ERROR notice, `eval` with an ERROR notice, `add-feature`, `add-custom-feature` or `update-feature` with status ERROR |
| 2 | usage: arguments, ids, JSON, a bad `ONSHAPE_BASE_URL` or `ACES_ONSHAPE_ACES_CMD`, no project or the wrong project, a write refused (a foreign target, a public document) |
| 3 | auth: no keys, partial or malformed keys, a credentials file with a bad mode or owner, HTTP 401 or 403 from the Onshape API (a download host that refuses a redirected request, which carries no Onshape credentials, is exit 1) |
| 4 | class refused |
| 130 | interrupted |

Retries: 429 is retried up to five times, honouring `Retry-After` (seconds or an HTTP date) and refusing to wait
longer than 60 s; 5xx and connection failures are retried only on idempotent calls (three times). A call that creates
something (a document, a studio, a feature, a version) is never retried after its request may have been sent: the
error then says the request may or may not have been applied, so check the document before retrying.

## MCP tools

`aces-onshape-mcp` speaks JSON-RPC 2.0 over stdio, one message per line (MCP protocol versions 2025-11-25, 2025-06-18,
2025-03-26 and 2024-11-05). Copy the `aces-onshape` entry from `/usr/share/aces/cad-onshape/mcp.json.example` into the
agent's MCP configuration and replace `<project>`. The 17 tools mirror the CLI:

| Tool | Properties (required in bold) | Writes in Onshape |
|---|---|---|
| `onshape_whoami` | none | nothing |
| `onshape_documents` | `query`, `limit` (1-20) | nothing |
| `onshape_create_document` | **`name`**, `description`, `project` | a new document (private when the plan allows) |
| `onshape_elements` | **`did`, `wvm`, `wvmid`**, `element_type` | nothing |
| `onshape_featurestudio_read` | **`did`, `wvm`, `wvmid`, `eid`** | nothing |
| `onshape_featurestudio_write` | **`did`, `wid`, `code`**, `eid`, `name`, `project`, `confirm_foreign`, `allow_public` | creates a Feature Studio (no `eid`); replaces its whole source |
| `onshape_featurescript_eval` | **`did`, `wvm`, `wvmid`, `eid`, `script`**, `queries`, `raw`, `project` | nothing |
| `onshape_features` | **`did`, `wvm`, `wvmid`, `eid`**, `raw` | nothing |
| `onshape_add_feature` | **`did`, `wid`, `eid`, `feature`**, `project`, `allow_public` | one feature |
| `onshape_add_custom_feature` | **`did`, `wid`, `eid`, `studio_eid`, `feature_type`**, `parameters`, `name`, `pin_version`, `replace_feature_id`, `project`, `confirm_foreign`, `allow_public` | one feature, or a replacement; with `pin_version` also a permanent version |
| `onshape_update_feature` | **`did`, `wid`, `eid`, `feature_id`, `feature`**, `project`, `confirm_foreign`, `allow_public` | replaces one feature |
| `onshape_delete_feature` | **`did`, `wid`, `eid`, `feature_id`**, `project`, `confirm_foreign`, `allow_public` | deletes one feature (recoverable from the document history) |
| `onshape_version_create` | **`did`, `name`**, `wid`, `description`, `project`, `allow_public` | a permanent version |
| `onshape_massproperties` | **`did`, `wvm`, `wvmid`, `eid`** | nothing |
| `onshape_export_step` | **`did`, `wvm` (`w` or `v`), `wvmid`, `eid`**, `project`, `label` | nothing (a translation job with `storeInDocument: false`); writes local files |
| `onshape_export_stl` | **`did`, `wvm`, `wvmid`, `eid`**, `project`, `label`, `units` | nothing; writes local files |
| `onshape_pull` | **`ref`**, `project` | nothing; writes local files |

- **Descriptions name every write.** Each starts with `WRITES to Onshape:`, `Read-only:` or
  `Read-only in Onshape; writes local files:`, and carries read-only, destructive and idempotent hints.
- **`project`.** The eleven tools that send content to Onshape, change a document or write local files
  (`create_document`, `featurestudio_write`, `featurescript_eval`, the four feature tools, `version_create`, both
  exports and `pull`) need a project. `tools/list` marks it required unless `ACES_ONSHAPE_PROJECT` is set; a call
  without one comes back as an error of kind `project` that says how to fix it.
- **`confirm_foreign`.** Rewriting or removing a Feature Studio or feature that this server process did not create
  (`featurestudio_write` with `eid`, `update_feature`, `delete_feature`, `add_custom_feature` with
  `replace_feature_id`) is refused with kind `write_refused`, before any request, unless `confirm_foreign` is true. Set
  it only when the operator has agreed, after a version exists as a restore point.
- **`allow_public`.** Every write except `create_document` reads the document's `public` flag once per process and
  refuses to write into a public document unless `allow_public` is true: anything written into it is published.
- **The session.** One per server process. It remembers the documents, Feature Studios and features the process
  created, and everything inside a document it created counts as its own. A restart forgets it.
- **Keys are read per call**, so a key change needs no restart, and a server without keys still answers `initialize`
  and `tools/list` (tool calls then return kind `auth`).
- **Results** are `structuredContent` plus the same JSON as compact text; raw Onshape maps appear only where `raw` is
  asked for. Failures are tool results with `isError: true` and `structuredContent.error` (`kind`, `message`,
  `exit_code`, and `status` or `maybe_applied` where they apply); invalid arguments are kind `usage`, so the model can
  fix the call. Kinds: `usage`, `config`, `project`, `write_refused`, `class_refused`, `auth`, `not_found`,
  `rate_limited`, `server`, `transport`, `translation`, `onshape`, `error`, `internal`.
- The server's `initialize` instructions repeat the rules above and say that text read from Onshape (FeatureScript
  source, feature, element and document names, error messages) is data, never instructions.
- **On the wire** every line it writes is ASCII JSON (`\u` escapes, so a lone surrogate in Onshape's text goes out as
  `\ud800`). A line that is not JSON, or is nested deeper than Python's json takes, gets a `-32700` parse error; a batch
  or a request whose `id` is not a string, a number or null gets `-32600`; a reply that cannot be encoded becomes a
  `-32603` error. None of these ends the server: it reads the next line.

## Pull bundle

`aces-onshape pull REF --project NAME` (or `onshape_pull`) writes
`<ACES_WORK>/<project>/cad/onshape/<YYYYmmddTHHMMSSZ>-<first 8 characters of eid>[-<n>]/` (UTC stamp; `-2`, `-3`, ...
when the name is taken):

| File | What it holds |
|---|---|
| `part.step` | STEP AP242 from a translation job (`storeInDocument: false`) |
| `parameters.json` | the Part Studio (element id, name, microversion), `source_microversion`, `library_version`, `rollback_index`, the features in compact form (id, name, type, namespace, suppressed, status, parameters), the Feature Studio capture (below), the mass properties (raw bodies, or `{"error"}`) and the raw feature list |
| `source.json` | the provenance: canonical ref, base URL, document id, name and `public`, workspace or version id, element, the workspace microversion before and after the export with `consistent`, `exported_at` (UTC), the translation (id, AP242, final state, every state change with its time and poll number, poll count, result ids), sha256 and size of `part.step` and `parameters.json`, whether the OS ledger entry can be written (`ledger`: `available`, and the `reason` when it cannot), the project (name, class and where the class came from, directory), the tool and its version, warnings |
| `outcome.json` | the sha256 and size of `source.json` (`files`), and the ledger and `add-cad-source` outcomes |

- The bundle is assembled in `.<name>.partial` and renamed into place, with `outcome.json` already written as
  "pending", so a crash leaves either no bundle or a complete one. `source.json` is written once and never rewritten;
  `outcome.json` is the only file that is (once, after the rename). Hashes are sha256 of the bytes on disk.
- `source.json` cannot hold its own hash, so `outcome.json` records it, and the `cad.pull` ledger entry records the
  hashes of `part.step`, `parameters.json` and `source.json`. `outcome.json` is the one status file that no other file
  hashes: it says what happened after the bundle was complete.
- When the workspace changed during the pull, `consistent` is false and a warning says so: pull a version for an
  immutable source.

**The Feature Studio capture** (`parameters.json` `feature_studios`) holds exactly this:

- **Roots:** every distinct non-empty `namespace` over the Part Studio's features and their sub-features
  (recursively). A workspace-form namespace (`e<eid>::m<mid>`) names a studio in the pulled document at the pulled
  workspace or version; a version-form namespace (`d<did>::v<vid>::e<eid>::m<mid>`) names a studio at that version of
  that document. A namespace that is neither is listed under `unrecognised_namespaces` and not fetched.
- **Each studio,** read once, with: `document_id`, `wvm`, `wvmid`, `element_id`, `name`, `reached_by` (every namespace
  or importing studio that led to it), `microversion_expected` (the `m` part of the namespace, or the import's
  `version`), `microversion_current` (the element's microversion there), `namespace_current` (whether they are equal),
  `read_from`, `source_microversion`, `contents_sha256`, the full source as `contents`, and its `imports`.
- **When `namespace_current` is false,** the studio changed after the feature was instantiated: the captured source is
  the current one, not necessarily the code the feature regenerated with, and a warning says so. The source at the
  pinned microversion is not read (open question 17).
- **Imports** are parsed from each studio's source (comments stripped): path, version, kind (`std`, `workspace`,
  `linked`, `unrecognised`), alias, whether exported, and the ids. They are followed transitively: a workspace import
  is the element in the importing studio's own context, a linked import is `<did>/<vid>/<eid>` at that version.
- **Bounds:** an import is fetched only when its element is a Feature Studio, at a depth of at most 3 (the roots are
  depth 0), and while fewer than 20 studios have been read in total. Each import records `fetched` and, when not
  fetched, a `reason`: `std library`, `not a Feature Studio (<type>)`, `depth limit 3`, `studio limit 20`,
  `element not found`, `unrecognised path`, or the error text. A fetched import also records `current` (whether its
  `version` equals the element's microversion).
- `feature_studios_complete` is false when any root or import that may hold the part's source was not captured: a
  depth or studio limit, an error, an element that was not found, an unrecognised import path or namespace, or a
  namespace that names something other than a Feature Studio. Standard-library imports and imports of other element
  types (data tabs) do not count. A studio that fails to read is an entry with `error`, and the pull continues; an auth
  error stops it.

**Ledger and CadSource.** After the rename the pull runs `aces ledger record cad.pull --json -` when the installed
`aces` offers `ledger record`; it asks `aces ledger record --help` once, before it writes `source.json`, and records the
answer there as `ledger`. The payload, well under 64 KiB and free of secrets, holds the source, the ref, the project,
the bundle path, the document name, the base URL, `exported_at`, the microversions before and after, `consistent`, the
translation id, and the sha256 of `part.step`, `parameters.json` and `source.json`. It then runs
`aces project add-cad-source <project> cad/onshape/<name>/part.step` when that command exists. aces-base 0.1.0 has
neither command, so with it `source.json` (`ledger.available` false, with the reason) says that the OS ledger entry was
not written, and `outcome.json` and the result say that neither the ledger entry nor the CadSource record was written,
and why.

**Exports** (`export step`, `export stl`) write `<ACES_WORK>/<project>/cad/onshape/exports/<stamp>-<eid8>[-<label>][-<n>].step`
(or `.stl`) with a `<file>.json` sidecar (sha256, size, ref, document with `public`, microversion, the translation or the
STL options, project, tool). Names never collide (exclusive create, then `-2`, `-3`, ...). Exports write no ledger entry;
only pull does.

## Class gate

Onshape is a cloud service, so VISION.md section 5 step 2 keeps it away from `sovereign` and `controlled` projects. The
gate is an allowlist: only `personal`, `open` and `commercial` pass, so an unknown or missing class is refused too.

- **Which calls.** The project tools (create-document, fs write, eval, add-feature, add-custom-feature, update-feature,
  delete-feature, version, export step, export stl, pull) need a project and run the gate for it. The read tools
  (whoami, documents, elements, fs read, features, massprops) run it only for a bound project.
- **Order.** Arguments are validated, then the gate runs, then the client is built, then the operation runs; a failure
  at any step sends no request at all. Every operation runs the gate again as its first statement, so library callers
  get it too.
- **Where the class comes from.** `aces project show --json <name>` when an `aces` CLI is available, else the top-level
  `class:` line of `<ACES_WORK>/<name>/aces.yaml` (a file with more than one is refused rather than guessed). The
  `aces` CLI runs with `ACES_WORK` set to the adapter's own project root (`~/Work` when the variable is unset or empty),
  so the class always comes from the project whose directory receives the files. A missing
  project is refused with the `aces project new <name> --class <personal|open|commercial>` command to run. Project
  names are letters, digits, `.`, `_` and `-`, at most 64 characters, starting with a letter or digit.
- **The refusal** (exit 4, kind `class_refused`):
  `project '<name>' is class '<class>': Onshape is a cloud service, allowed only for personal, open and commercial projects and never for sovereign or controlled ones (VISION.md section 5, step 2); use build123d or the voxel kernel for this project`.
- **`ACES_ONSHAPE_PROJECT`** binds the CLI and the MCP server to one project. A different `--project` or `project`
  argument is refused (exit 2, kind `project`), and the read tools run the gate for the bound project, so a server bound
  to a sovereign project refuses even `whoami`. The `<project>` placeholder in `mcp.json.example` fails the name rule,
  so an unedited copy refuses every project tool.

## The official FeatureScript MCP Server

Onshape publishes its own FeatureScript MCP Server. What is known comes from Onshape's Tech Tip "How to Connect the
FeatureScript MCP Server and Claude Code" (8 Sep 2026) and its blog posts of 3, 9 and 30 Sep 2026:

- It launched through Onshape Labs, an early-access program, and is available through the Onshape App Store
  (`https://cad.onshape.com/appstore/apps/Onshape%20Labs/6a29aea7c03f8bf659841734`); the user subscribes there first.
- Claude Code connects with `claude mcp add --transport http onshape https://fs-mcp.labs.onshape.app/mcp`. The
  `onshape-featurescript` entry in `mcp.json.example` gives the same server in the `mcpServers` format
  (`"type": "http"`, `"url": "https://fs-mcp.labs.onshape.app/mcp"`), for a project's `.mcp.json` or another client
  that reads that format.
- Sign-in runs in the browser: per the Tech Tip, launching Claude Code asks you to authorize the Onshape subscription
  on an "Authorize" page, and `/mcp` then brings up the authentication screen that connects the Onshape account. It
  uses neither the API keys nor `onshape.env`. Onshape's texts do not name the protocol: OAuth is inferred from MCP's
  authorization model and is checked in step 14 of the live check in `docs/PACKAGES.md`.
- Its calls draw on the user's API allocation, or on the company's when the user asks for that.
- Per Onshape, the assistant can write FeatureScript directly into a document, review errors, iterate on the code and
  inspect the geometry the code produces.
- Unknown: its tool list and OAuth scopes. It has not been connected from this machine (no Onshape account here).
- The ACES class gate cannot see its calls, so it must never be used for sovereign or controlled projects; that rule is
  the operator's and the agent's to keep. The skill prefers it for Feature Studio editing once the operator has
  connected it and the agent has checked the class, and still uses `aces-onshape` for the class-gated pull and exports,
  which write the evidence bundle and the ledger entry.

## Installed files

| Path | What |
|---|---|
| `/usr/bin/aces-onshape` | the CLI |
| `/usr/bin/aces-onshape-mcp` | the stdio MCP server |
| `/usr/lib/python3.*/site-packages/aces_onshape/` | the package |
| `/usr/share/aces/cad-onshape/skills/aces-cad-onshape/SKILL.md` | the agent skill |
| `/usr/share/aces/cad-onshape/mcp.json.example` | MCP client entries |
| `/usr/share/aces/cad-onshape/tests/` | the unit tests and the fake server |
| `/usr/share/doc/aces-cad-onshape/README.md` | this file |
| `/usr/share/licenses/aces-cad-onshape/LICENSE` | MIT |

`aces-omarchy-setup` links the skill into each agent's skills directory with every other `/usr/share/aces/*/skills/*`.

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s src/aces-cad-onshape/tests        # from the repo root
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s /usr/share/aces/cad-onshape/tests  # installed
```

The package build's `check()` runs the same tests, and `bin/install-test` runs the installed copy after checking that
`aces-onshape whoami` exits 3 without keys.

- **The fake server** (`tests/fake_onshape.py`) is an `http.server` on `127.0.0.1` in a thread that implements every
  endpoint the client uses, with in-memory documents, workspaces, versions (snapshots), elements, Feature Studio
  sources, features, translations (ACTIVE, then DONE on the second poll, or FAILED) and external data. The STL route
  answers 307 to a download URL, optionally on a second fake server so that the cross-origin rule can be checked. Every
  API route checks the Basic header. Knobs inject statuses, lost responses, delays, feature status, eval results, bad
  ids and public documents. Every fixture value is labelled as a fixture.
- **The fake aces CLI** (`tests/fake_aces.py`) stands in for `aces project show`, `aces ledger record` and
  `aces project add-cad-source`, with and without the newer commands.
- **Isolation.** Every test removes the `ONSHAPE_*` and `ACES_*` variables and the proxy variables, points `HOME`,
  `USERPROFILE`, `ACES_USER_CONFIG`, `ACES_WORK` and `ACES_STATE` at its temp directory, and sets
  `ACES_ONSHAPE_ACES_CMD=[]` unless it points it at the fake aces CLI. Subprocesses get the same environment. A guard
  installed at import refuses any connection to a host other than `127.0.0.1`, `::1` or `localhost` and fails the test
  that tried, and clients of the loopback fake use no proxy. So the tests never read the operator's keys, `onshape.env`
  or real `aces`, and never reach Onshape, even on a machine that has real keys configured.

## Verified and not verified

**Verified against Onshape's public OpenAPI spec** (OpenAPI 3.0.1, Onshape REST API `1.221.89963-13d10d36cb23`,
server `https://cad.onshape.com/api/v17`, saved 2026-10-09): every path, method, request body and response field the
client uses, and the facts the design rests on:

- translation states are `ACTIVE`, `DONE` and `FAILED` (there is no `PENDING`); a failure carries `failureReason`;
- `GET .../stl` answers with a 307 redirect to the file, so the client follows redirects itself and forwards
  `Authorization` only to the base URL's origin or between https `*.onshape.com` hosts;
- the custom-feature namespace forms are documented only for `/fstable` custom tables (`e<eid>::m<mid>` in the same
  workspace, `d<did>::v<vid>::e<eid>::m<mid>` across workspaces): inferred for features, not verified;
- only the eval response documents `notices`; the Feature Studio write response does not;
- eval takes only lambda expressions, and `queries` is optional;
- `BTDocumentInfo.public` exists (the public-document guard reads it).

**Verified against the FeatureScript documentation:** every FeatureScript fact the skill states (functions, map keys,
bounds, units, imports, `println` and `regenError` behaviour).

**Not verified against a live account.** The tests run only against the fake server in `tests/`, and the code has not
run against a live Onshape account. These open questions stay open until the live check in the aces-os repository's
`docs/PACKAGES.md` answers them:

1. Whether a REST write to a Feature Studio commits it (in Onshape's UI, changes reach other tabs only after Commit).
2. Whether the Part Studio featurespecs report, in `namespace`, the string `POST .../features` needs for a custom
   feature (the adapter prefers it when it names the same studio).
3. Whether `POST .../features` accepts the `/fstable` namespace forms, including a version of the same document, and
   whether an instance follows later studio writes or must be re-pointed (`replace_feature_id`, `--replace`).
4. Whether a parameter whose featurespec has no `defaultValue` may be left out of the instance (the adapter omits it).
5. Whether the studio featurespecs' `namespace` and `defaultValue` are usable verbatim.
6. Where the compile errors of a studio write appear (the adapter reads the featurespecs after the write, the feature
   status and an eval probe).
7. Whether `makeId("<feature id>")` addresses a top-level feature for `getFeatureError`, `getFeatureWarning` and
   `getFeatureInfo` in the eval probe.
8. The shape of error bodies (the adapter shows a JSON `message` when there is one, else the start of the body).
9. Whether 429 answers carry `Retry-After`, and which status plan and API-allocation limits answer with.
10. The eval `queries` format and what `console` holds (println output from the lambda is expected there).
11. The target of the `/stl` 307 redirect, whether it needs `Authorization`, and whether the lower-case `units` value
    is accepted.
12. Translation timing, when `resultDocumentId` is set, and the formats of translation and external-data ids
    (validated loosely and URL-quoted).
13. The official FeatureScript MCP Server's tool list and OAuth scopes (its connection details are known from the Tech
    Tip; it has not been connected from this machine).
14. Mass-property units (the schema states none).
15. Free-plan public documents: whether `isPublic: false` holds on a paid plan and is overridden on a free one.
16. The namespace a `BTMParameterEnum-145` needs: the feature's namespace for an enum the studio declares with
    `export enum`, Onshape's reported namespace for a std enum the studio re-exports.
17. Whether an element microversion id is a valid document microversion id, which would let the pull read the source a
    stale namespace points at (it reads the current source and warns).
18. How much API allocation each operation consumes (the skill's call counts are estimates).

## Not in this stage

- `aces cad pull onshape://...` in aces-base: use `aces-onshape pull`.
- Importing meshes back into Onshape: return-to-CAD means rebuilding the parametric envelope in FeatureScript.
- OAuth for this adapter (the REST API's OAuth2 authorization-code flow): it uses API keys. The official server signs
  in through the browser instead (see above).
- Onshape write-back of a proposed version (the `CadSource` push), deferred in VISION.md.
