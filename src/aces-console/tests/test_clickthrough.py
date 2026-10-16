"""Full click-through of the aces console UI (Playwright): every plate, every interactive control, both widths.

The fixture devserver (tests/harness/devserver.py, --vm-work when the store is present) is driven with a real
headless Chromium. For every plate the suite walks the controls a person would touch — tabs, cards, selects,
buttons, forms, chips, deep links — and asserts:

- zero browser console errors, zero unhandled page errors;
- no 4xx/5xx response except the deliberate, allow-listed fixture refusals (a POST the devserver refuses with
  503 "fixture", or the plan deep link's honest 404), each of which the plate must present as a refusal/absence;
- the view renders its core content (per-plate anchors), and nothing overflows the viewport horizontally.

Consequential actions are exercised up to the fixture refusal (Generate plan, Switch mode prepare, Open, Verify);
"Stop console" is only driven to its enabled state, never pressed (the stop path is covered by unit tests, and
pressing it would kill the shared devserver mid-run). Skips cleanly when Playwright or a browser is absent.
"""
from __future__ import annotations

import json
import os
import re
import time
import unittest
import urllib.error
import urllib.request

try:
    from playwright.sync_api import sync_playwright

    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

HERE = os.path.dirname(os.path.abspath(__file__))
from .test_harness import DevServerProcess, vm_work_dir

PLATES = ["plan", "fabric", "projects", "pipeline", "solvers", "objects", "evidence", "ops"]
DESKTOP = (1280, 800)
PHONE = (375, 720)
SHOTS = os.path.join(HERE, "harness", "shots", "clickthrough")

# per-plate anchors that must be visible once the plate has mounted (core content rendered)
ANCHORS = {
    "plan": ["#plan-composer textarea.brief", "#plan-generate", "#plan-launch"],
    "fabric": ["#fab-mode", "#fab-cards", "#fab-fleet canvas.sky"],
    "projects": ["#proj-list", "#proj-detail"],
    "pipeline": ["#pipe-summary", "#pipe-experiments table"],
    "solvers": ["#sol-registry table", "#sol-capacity", "#sol-modes"],
    "objects": ["#obj-project", "#obj-runs"],
    "evidence": ["#evi-runs", "#evi-ledger", "#evi-chain"],
    "ops": ["#ops-switch", "#ops-switch .gates", "#ops-doctor", "#ops-stop"],
}

JS_OVERFLOW = """
() => {
  const iw = window.innerWidth;
  const sig = (e) => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (e.className && typeof e.className === "string" ? "." + e.className.trim().split(/\\s+/).slice(0, 3).join(".") : "");
  const clipped = (e) => { for (let a = e.parentElement; a; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o === "auto" || o === "hidden" || o === "scroll" || o === "clip") return true; } return false; };
  const out = [];
  for (const e of document.querySelectorAll("body *")) {
    if (e.closest("[hidden]")) continue;
    const r = e.getBoundingClientRect();
    if (r.width > 0 && r.right > iw + 0.5 && !clipped(e)) out.push({ el: sig(e), right: Math.round(r.right), width: Math.round(r.width), text: (e.textContent || "").trim().slice(0, 50) });
  }
  return { scrollWidth: document.documentElement.scrollWidth, innerWidth: iw, offenders: out.slice(0, 10) };
}
"""


def api(url, path):
    try:
        with urllib.request.urlopen(url + path, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


class Watch:
    """Collects console errors, page errors and failing requests for one page; allow-lists expected refusals."""

    def __init__(self, page):
        self.console_errors = []
        self.page_errors = []
        self.bad_responses = []
        self.failed_requests = []
        self.allowed = []   # (status, url substring) expectations; each may be consumed once
        self.consumed = []  # statuses of expectations a response consumed
        page.on("console", lambda m: self.console_errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.on("response", self._response)
        page.on("requestfailed", self._failed)

    def _response(self, r):
        if r.status < 400:
            return
        for i, (st, sub) in enumerate(self.allowed):
            if r.status == st and sub in r.url:
                del self.allowed[i]
                self.consumed.append(st)
                return
        self.bad_responses.append(f"{r.status} {r.url}")

    def _failed(self, r):
        # an EventSource the page closes itself (plate change, context close) is ERR_ABORTED on /api/events:
        # the expected close of a stream, not a failed request
        if "/api/events" in r.url and "ERR_ABORTED" in str(r.failure):
            return
        self.failed_requests.append(f"{r.url} {r.failure}")

    def allow(self, status, url_sub):
        self.allowed.append((status, url_sub))

    def problems(self):
        # a handled refusal (an allow-listed 4xx/503 the plate draws as a refusal) still makes Chromium log
        # "Failed to load resource: … status of NNN"; that log line is the refusal's echo, not an unhandled error
        consumed = list(self.consumed)
        console = []
        for e in self.console_errors:
            m = re.search(r"status of (\d+)", e)
            if m and int(m.group(1)) in consumed:
                consumed.remove(int(m.group(1)))
                continue
            console.append(e)
        out = []
        out += [f"console: {e}" for e in console]
        out += [f"pageerror: {e}" for e in self.page_errors]
        out += [f"response: {e}" for e in self.bad_responses]
        out += [f"requestfailed: {e}" for e in self.failed_requests]
        return out


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed (pip install playwright; playwright install chromium)")
class TestClickthrough(unittest.TestCase):
    """One devserver, one browser; a fresh page per test. Numbered so the walk is deterministic."""

    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            cls.pw.stop()
            raise unittest.SkipTest(f"no launchable browser: {str(e).splitlines()[0][:160]}")
        cls.srv = DevServerProcess()
        cls.url = cls.srv.url
        cls.vm = vm_work_dir()
        # API reads used to pick real ids for the walk
        cls.projects = (api(cls.url, "/api/projects").get("data") or {}).get("projects") or []
        cls.experiments = (api(cls.url, "/api/sim/experiments").get("data") or {}).get("experiments") or []
        cls.solvers = list(((api(cls.url, "/api/sim/registry").get("data") or {}).get("solvers") or {}).keys())
        cls.runs = (api(cls.url, "/api/golden/runs").get("data") or {}).get("runs") or []

    @classmethod
    def tearDownClass(cls):
        for thing in (getattr(cls, "srv", None), getattr(cls, "browser", None), getattr(cls, "pw", None)):
            if thing is None:
                continue
            try:
                (thing.stop if hasattr(thing, "stop") else thing.close)()
            except Exception:  # noqa: BLE001
                pass

    # ---------- helpers ----------
    def new_page(self, w=DESKTOP[0], h=DESKTOP[1]):
        ctx = self.browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, color_scheme="dark")
        page = ctx.new_page()
        watch = Watch(page)
        self.addCleanup(ctx.close)
        return page, watch

    def goto(self, page, plate, extra=""):
        page.goto(f"{self.url}/?plate={plate}{extra}", wait_until="load")
        page.wait_for_selector(f"body[data-ready='{plate}']", state="attached", timeout=30000)
        page.wait_for_selector("#stamp .part", state="attached", timeout=15000)
        page.wait_for_timeout(900)  # the first stamps of the stream settle the plate

    def shot(self, page, name):
        page.screenshot(path=os.path.join(SHOTS, name + ".png"))

    def assert_clean(self, watch, where):
        self.assertEqual(watch.problems(), [], f"{where}: " + " | ".join(watch.problems()))

    def assert_no_overflow(self, page, where):
        r = page.evaluate(JS_OVERFLOW)
        self.assertLessEqual(r["scrollWidth"], r["innerWidth"] + 1, f"{where}: horizontal overflow {r['scrollWidth']} > {r['innerWidth']}: {r['offenders']}")

    def assert_anchors(self, page, plate):
        for sel in ANCHORS[plate]:
            self.assertTrue(page.locator(sel).first.is_visible(), f"plate {plate}: anchor {sel} not visible")

    def wait_settled_text(self, page, sel, busy_marker, timeout=20000):
        """Wait for sel's text to exist and to stop showing its busy marker (a result arrives after 'requesting…'/'verifying…')."""
        deadline = time.time() + timeout / 1000
        txt = ""
        while time.time() < deadline:
            loc = page.locator(sel).first
            if loc.count():
                txt = loc.inner_text()
                if busy_marker not in txt:
                    return txt
            page.wait_for_timeout(200)
        self.fail(f"{sel}: still showing {busy_marker!r} after {timeout} ms (last text: {txt!r})")

    # ---------- the walk ----------
    def test_01_every_plate_renders_its_core_content(self):
        page, watch = self.new_page()
        for plate in PLATES:
            self.goto(page, plate)
            self.assert_anchors(page, plate)
            body = page.inner_text(f"#body-{plate}")
            self.assertGreater(len(body.strip()), 20, f"plate {plate} rendered nearly empty")
            # the instrument-board shape: every plate is panels (.blk), and a panel's long table scrolls
            # inside the panel (max-height + overflow) rather than stretching the plate into a document
            panels = page.evaluate(f"document.querySelectorAll('#body-{plate} .blk').length")
            self.assertGreater(panels, 0, f"plate {plate}: no instrument panels rendered")
            wrap = page.evaluate(f"(() => {{ const w = document.querySelector('#body-{plate} .blk .table-wrap'); return w ? getComputedStyle(w).overflowY : null; }})()")
            if wrap is not None:
                self.assertEqual(wrap, "auto", f"plate {plate}: a panel table does not scroll inside its panel")
            self.assert_no_overflow(page, f"desktop {plate}")
        self.assert_clean(watch, "all plates render")

    def test_02_deep_links(self):
        page, watch = self.new_page()
        # plan deep link to an unknown id: an honest absence, not an error (404 allow-listed)
        watch.allow(404, "/api/plan/")
        self.goto(page, "plan", "&plan=no-such-plan")
        self.assertIn("plan", page.inner_text("#plan-head").lower())
        self.assertTrue(page.locator("#plan-head .note-inline").count() >= 1, "plan deep link draws its absence")
        # records deep links against real ids
        if self.projects:
            name = self.projects[0]["name"]
            self.goto(page, "projects", f"&project={name}")
            self.assertIn(name, page.inner_text("#proj-detail"))
        if self.experiments:
            eid = self.experiments[0]["id"]
            self.goto(page, "pipeline", f"&experiment={eid}")
            self.assertIn(eid, page.inner_text("#pipe-experiment"))
        if self.solvers:
            self.goto(page, "solvers", f"&solver={self.solvers[0]}")
            self.assertIn(self.solvers[0], page.inner_text("#eye"))
        if self.runs:
            r = self.runs[0]
            self.goto(page, "evidence", f"&project={r['project']}&run={r['run_id']}")
            self.assertIn(r["run_id"], page.inner_text("#evi-run"))
            self.goto(page, "objects", f"&project={r['project']}")
            self.assertIn(r["project"], page.inner_text("#obj-project"))
        self.assert_clean(watch, "deep links")

    def test_03_plan_composer_full_flow(self):
        page, watch = self.new_page()
        self.goto(page, "plan")
        # empty brief is refused locally, before any POST
        page.click("#plan-generate")
        page.wait_for_selector("#plan-composer .note-inline", timeout=5000)
        self.assertIn("empty", page.inner_text("#plan-composer .note-inline").lower())
        # attachments: by button, by Enter, and removed again
        page.fill("#plan-composer input.type", "run 20261010T084129Z-b3ac99")
        page.click("#plan-composer .actrow >> text=add ref")
        page.fill("#plan-composer input.type", "work/parts/bracket.step")
        page.press("#plan-composer input.type", "Enter")
        self.assertEqual(page.locator("#plan-composer .chip").count(), 2)
        page.locator("#plan-composer .chip .x").first.click()
        self.assertEqual(page.locator("#plan-composer .chip").count(), 1)
        # generate: the fixture devserver refuses the consequential POST (503); the plate must present the refusal
        watch.allow(503, "/api/plan")
        page.fill("#plan-composer textarea.brief", "a 1500 kg six-wheeled off-roader for rough terrain")
        page.click("#plan-generate")
        page.wait_for_selector("#plan-composer .note-inline", timeout=10000)
        self.assertFalse(page.locator("#plan-generate").is_disabled(), "composer usable again after the refusal")
        # launch is disabled with its reason, and the reason is visible text, not only a title
        self.assertTrue(page.locator("#plan-launch").is_disabled())
        why = page.inner_text("#plan-run .why")
        self.assertTrue(why.strip(), "the Launch button's reason is printed")
        # planner status line is present and honest about availability
        self.assertIn("planner", page.inner_text("#plan-composer").lower())
        self.shot(page, "plan-composer-refusal")
        self.assert_no_overflow(page, "plan composer flow")
        self.assert_clean(watch, "plan composer")

    def test_04_fabric_controls(self):
        page, watch = self.new_page()
        self.goto(page, "fabric")
        cards = page.locator("#fab-cards .card")
        self.assertGreater(cards.count(), 0, "no GPU cards rendered")
        cards.first.click()
        page.wait_for_timeout(300)
        self.assertIn("GPU", page.inner_text("#eye"))
        cards.first.click()  # toggle off: the inspector returns to the fleet view
        page.wait_for_timeout(300)
        self.assertIn("fleet", page.inner_text("#eye").lower())
        # the one consequential action: refused by the fixture with its reason shown
        watch.allow(503, "/api/fabric/refresh")
        page.click("#fab-manifest >> text=Refresh fabric")
        txt = self.wait_settled_text(page, "#fab-manifest .result", "requesting")
        self.assertIn("503", txt)
        self.assert_no_overflow(page, "fabric")
        self.assert_clean(watch, "fabric controls")

    def test_05_projects_controls(self):
        page, watch = self.new_page()
        self.goto(page, "projects")
        cards = page.locator("#proj-list .card")
        if not cards.count():
            self.skipTest("no projects in the work store")
        name = cards.first.locator(".nm").inner_text()
        cards.first.click()
        page.wait_for_timeout(600)
        self.assertIn(name, page.inner_text("#proj-detail"))
        self.assertIn("golden runs", page.inner_text("#proj-detail").lower())
        # the "Evidence" button of the first golden run navigates to the run on the Evidence plate
        btn = page.locator("#proj-detail table >> text=Evidence").first
        if btn.count():
            btn.click()
            page.wait_for_selector("body[data-ready='evidence']", timeout=30000)
            page.wait_for_timeout(600)
            self.assertIn("plate=evidence", page.url)
            self.assertIn("run=", page.url)
        self.assert_no_overflow(page, "projects")
        self.assert_clean(watch, "projects controls")

    def test_06_pipeline_drilldown(self):
        page, watch = self.new_page()
        self.goto(page, "pipeline")
        rows = page.locator("#pipe-experiments tbody tr")
        if not rows.count():
            self.skipTest("the fixture lists no experiments")
        page.locator("#pipe-experiments tbody tr td.nm button").first.click()
        page.wait_for_timeout(900)
        self.assertNotIn("no experiment selected", page.inner_text("#pipe-experiment"))
        case_btn = page.locator("#pipe-experiment table >> nth=0 >> xpath=..")
        # open the first case whose button exists in the cases table
        btns = page.locator("#pipe-experiment .table-wrap button")
        if btns.count():
            btns.first.click()
            page.wait_for_timeout(900)
            self.assertNotIn("no case selected", page.inner_text("#pipe-case"))
        self.assert_no_overflow(page, "pipeline drilldown")
        self.assert_clean(watch, "pipeline drilldown")

    def test_07_solvers_controls(self):
        page, watch = self.new_page()
        self.goto(page, "solvers")
        btns = page.locator("#sol-registry tbody td.nm button")
        if not btns.count():
            self.skipTest("the registry lists no solvers")
        nm = btns.first.inner_text()
        btns.first.click()
        page.wait_for_timeout(300)
        self.assertIn(nm, page.inner_text("#eye"))
        self.assertIn("validation", page.inner_text("#columnBody").lower())
        self.assert_no_overflow(page, "solvers")
        self.assert_clean(watch, "solvers controls")

    def test_08_objects_controls(self):
        page, watch = self.new_page()
        self.goto(page, "objects")
        if not self.projects:
            self.skipTest("no projects in the work store")
        sel = page.locator("#objProject")
        if not sel.count():
            self.skipTest("no project selector (work store absent)")
        # choose the project with CAD runs if there is one
        options = sel.locator("option").all_inner_texts()
        values = sel.locator("option").evaluate_all("(os) => os.map((o) => o.value)")
        pick = values[options.index(next((o for o in options if "cad-vm" in o), options[0]))]
        sel.select_option(pick)
        page.wait_for_timeout(900)
        self.assert_no_overflow(page, "objects project")
        # open a CAD run
        run_btns = page.locator("#obj-runs tbody td.nm button")
        if run_btns.count():
            run_btns.first.click()
            page.wait_for_timeout(600)
            self.assertNotIn("no run selected", page.inner_text("#obj-run"))
        # check a bundle for real (check_bundle.py runs on this machine; a refusal is fine, silence is not)
        check = page.locator("#obj-bundles >> text=Check bundle").first
        if check.count():
            watch.allow(503, "/api/cad/bundle-check")
            watch.allow(409, "/api/cad/bundle-check")
            check.click()
            page.wait_for_selector("#obj-bundles .result", timeout=60000)
            txt = page.locator("#obj-bundles .result").first.inner_text()
            self.assertTrue(txt.strip(), "bundle check produced no visible result")
        # the STL viewer: loads the mesh, or says in words why not; never an uncaught error
        view = page.locator("#obj-bundles >> text=View part.stl").first
        if view.count():
            view.click()
            page.wait_for_timeout(2500)
            note = page.inner_text("#objViewerNote")
            self.assertTrue(note.strip(), "the viewer note is empty after View part.stl")
            self.shot(page, "objects-stl")
        self.assert_no_overflow(page, "objects")
        self.assert_clean(watch, "objects controls")

    def test_09_evidence_controls(self):
        page, watch = self.new_page()
        self.goto(page, "evidence")
        if not self.runs:
            self.skipTest("no golden runs in the work store")
        # the project filter narrows the table
        sel = page.locator("#eviProject")
        if sel.count():
            sel.select_option(self.runs[0]["project"])
            page.wait_for_timeout(600)
        page.locator("#evi-runs tbody td.nm button").first.click()
        page.wait_for_timeout(900)
        self.assertIn("no grade is a sign-off", page.inner_text("#evi-run"))
        self.assert_no_overflow(page, "evidence run")
        self.shot(page, "evidence-run")
        # Verify runs the real tool; the result (ok or the tool's refusal) must be drawn
        # (the button itself, not the block's "verify · aces golden verify…" caption, which text= would match first)
        watch.allow(503, "/api/golden/verify")
        watch.allow(409, "/api/golden/verify")
        page.locator("#evi-run .actrow button").first.click()
        txt = self.wait_settled_text(page, "#evi-run .result", "verifying", timeout=120000)
        self.assertTrue(txt.strip())
        # Verify ledger (a read of the desktop ledger)
        watch.allow(404, "/api/ledger/verify")
        watch.allow(503, "/api/ledger/verify")
        page.locator("#evi-ledger >> text=Verify ledger").first.click()
        page.wait_for_selector("#evi-ledger .result", timeout=30000)
        self.assertTrue(page.locator("#evi-ledger .result").first.inner_text().strip())
        self.assert_no_overflow(page, "evidence")
        self.assert_clean(watch, "evidence controls")

    def test_10_ops_controls(self):
        page, watch = self.new_page()
        self.goto(page, "ops")
        # every gate printed; the fixture gate holds, so the switch button stays disabled with the reason visible
        gates = page.locator("#ops-switch .gate")
        self.assertGreater(gates.count(), 5, "gates not printed")
        self.assertTrue(page.locator("#ops-switch >> text=Switch mode…").is_disabled())
        self.assertIn("fixture", page.inner_text("#ops-switch").lower())
        # choosing a target updates the gates and the inspector
        sel = page.locator("#opsTarget")
        values = [v for v in sel.locator("option").evaluate_all("(os) => os.map((o) => o.value)") if v]
        self.assertTrue(values, "no target modes offered")
        sel.select_option(values[0])
        page.wait_for_timeout(400)
        self.assertIn(values[0], page.inner_text("#eye"))
        # prepare is refused by the fixture only when every other gate passes; either way a session line must appear
        btn = page.locator("#ops-switch >> text=Switch mode…")
        if not btn.is_disabled():
            watch.allow(503, "/api/rig/switch/prepare")
            btn.click()
            page.wait_for_timeout(900)
        self.assert_no_overflow(page, "ops switch")
        # doctor: the real offline run, drawn verbatim
        page.locator("#ops-doctor >> text=Run doctor").click()
        page.wait_for_selector("#ops-doctor pre.log.verbatim, #ops-doctor .result", timeout=120000)
        # an Open button posts and draws the refusal (the button, not the table's "open" header, which text= matched first)
        open_btn = page.locator("#ops-links table button").first
        if open_btn.count() and open_btn.is_enabled():
            watch.allow(503, "/api/system/open")
            open_btn.click()
            page.wait_for_selector("#ops-links .result", timeout=10000)
        # stop: typing arms the button; the button is never pressed here (it would stop the shared devserver)
        page.fill("#ops-stop input.type", "stop")
        self.assertFalse(page.locator("#ops-stop .actrow button").is_disabled(), "typed 'stop' did not arm the button")
        page.fill("#ops-stop input.type", "")
        self.assertTrue(page.locator("#ops-stop .actrow button").is_disabled(), "clearing the text did not disarm the button")
        self.assert_no_overflow(page, "ops")
        self.assert_clean(watch, "ops controls")

    def test_11_keyboard_tabs(self):
        page, watch = self.new_page()
        self.goto(page, "fabric")
        page.focus("#tab-fabric")
        reached = []
        for _ in range(len(PLATES)):
            active = page.evaluate("document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.plate : null")
            if active:
                page.wait_for_selector(f"body[data-ready='{active}']", state="attached", timeout=30000)
                reached.append(active)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(350)
        self.assertEqual(sorted(set(reached)), sorted(PLATES), f"keyboard did not reach every plate: {reached}")
        self.assert_clean(watch, "keyboard tabs")

    def test_12_phone_width_every_plate(self):
        page, watch = self.new_page(*PHONE)
        # the rail is the 56 px bottom bar at phone width
        self.goto(page, "fabric")
        rail = page.locator("#rail").bounding_box()
        self.assertIsNotNone(rail)
        self.assertAlmostEqual(rail["height"], 56, delta=2, msg=f"phone rail is not the bottom bar: {rail}")
        for plate in PLATES:
            self.goto(page, plate)
            self.assert_anchors(page, plate)
            self.assert_no_overflow(page, f"phone {plate}")
        # and the plan composer stays usable at 375 px
        self.goto(page, "plan")
        page.fill("#plan-composer textarea.brief", "phone-width brief")
        self.assert_no_overflow(page, "phone plan typed")
        self.shot(page, "plan-phone")
        self.assert_clean(watch, "phone width")


if __name__ == "__main__":
    unittest.main()
