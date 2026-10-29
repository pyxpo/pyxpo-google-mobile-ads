"""pyxpo_google_mobile_ads web.js: simulated test ads in the page (full-screen show, banner and native views), run in node."""
import json
import shutil
import subprocess

import pytest

import pyxpo_google_mobile_ads

WEB_JS = pyxpo_google_mobile_ads.__path__[0] + "/web.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

# A tiny DOM: enough for web.js to build its elements, plus click() on them.
NODE = r"""
const fs = require("fs");
function el(tag) {
  const e = { tag, style: {}, children: [], attrs: {}, listeners: {}, textContent: "", offsetWidth: 360, disabled: false,
    appendChild(c) { this.children.push(c); c.parent = this; return c; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); },
    click() { for (const f of this.listeners.click || []) f(); },
    get firstChild() { return this.children[0] || null; },
    set innerHTML(v) { this.children = []; } };
  return e;
}
global.document = { createElement: el, createTextNode: (t) => ({ text: t }), body: el("body") };
const realInterval = setInterval;
global.setInterval = (f) => realInterval(f, 1);   // countdowns tick fast in tests
const views = {}; let mod;
global.pyxpoPage = { defineView(t, f) { views[t] = f; }, defineModule(n, f) { mod = f; } };
eval(fs.readFileSync(process.argv[2], "utf8"));
const events = [];
const page = { emit: (name, p) => events.push([name, p.id, p.event, p.data]) };
const out = {};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  out.notLoaded = await mod("show", [{ id: 9 }], page);
  out.load = await mod("load", [{ id: 1, type: "interstitial", unitId: "x" }], page);
  const shown = mod("show", [{ id: 1 }], page);
  const overlay = document.body.children[0];
  out.overlay = overlay.attrs["data-pyxpo-ad"];
  const close = overlay.children[2];
  out.closeEarly = close.disabled;
  close.click();                       // still counting down: ignored
  await wait(30);
  out.closeLater = close.disabled;
  close.click();
  out.inter = await shown;
  out.bodyAfter = document.body.children.length;
  out.again = await mod("show", [{ id: 1 }], page);   // an ad shows once

  await mod("load", [{ id: 2, type: "rewarded", unitId: "x" }], page);
  const r = mod("show", [{ id: 2 }], page);
  await wait(30);
  document.body.children[0].children[2].click();
  out.rewarded = await r;

  await mod("load", [{ id: 3, type: "app_open", unitId: "x" }], page);
  mod("destroy", [{ id: 3 }], page);
  out.destroyed = await mod("show", [{ id: 3 }], page);
  try { mod("load", [{ id: 4, type: "nope" }], page); } catch (e) { out.badType = e.message; }
  try { mod("nope", [{}], page); } catch (e) { out.badMethod = e.message; }
  out.consent = mod("consentRequestInfo", [{}], page);
  out.events = events;

  const bannerEvents = [];
  const host = el("div");
  const b = views.PxBannerAd(host, (n, d) => bannerEvents.push([n, d]));
  b.update({ unit_id: "u", size: "ANCHORED_ADAPTIVE_BANNER", request: {} }, []);
  await wait(400);
  b.update({ unit_id: "u" }, []);       // nothing changed: no reload
  await wait(400);
  b.dispose();
  out.banner = bannerEvents;

  const nativeEvents = [];
  const n = views.PxNativeAd(el("div"), (e, d) => nativeEvents.push([e, d]));
  n.update({ unit_id: "u", template: "small", background_color: 0xff112233 }, []);
  await wait(400);
  n.dispose();
  out.native = nativeEvents;
  const bs = views.PxBannerAd.bannerSize;
  out.sizes = [bs("BANNER", 0), bs("MEDIUM_RECTANGLE", 500), bs("ANCHORED_ADAPTIVE_BANNER", 0),
    bs("ANCHORED_ADAPTIVE_BANNER", 360), bs("ANCHORED_ADAPTIVE_BANNER", 1000), bs("INLINE_ADAPTIVE_BANNER", 360, 60),
    bs("250x120", 0)];
  out.css = [views.PxBannerAd.css(0xff112233, "x"), views.PxBannerAd.css(-16777216, "x"), views.PxBannerAd.css(null, "x")];
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    subprocess.run(["node", "--check", WEB_JS], check=True)
    t = tmp_path_factory.mktemp("adsweb") / "t.js"
    t.write_text(NODE)
    res = subprocess.run(["node", str(t), WEB_JS], check=True, capture_output=True, text=True, timeout=60)
    return json.loads(res.stdout)


def test_show_without_load(run):
    assert run["notLoaded"]["shown"] is False and run["notLoaded"]["error"] == "not_loaded"


def test_interstitial_overlay_counts_down_then_closes(run):
    assert run["load"] == {}
    assert run["overlay"] == "interstitial"
    assert run["closeEarly"] is True and run["closeLater"] is False
    assert run["inter"] == {"shown": True, "reward": None}
    assert run["bodyAfter"] == 0
    assert run["again"]["error"] == "not_loaded"
    assert [e[2] for e in run["events"] if e[1] == 1] == ["opened", "impression", "closed"]
    assert all(e[0] == "GoogleMobileAds.event" for e in run["events"])


def test_rewarded_earns_once(run):
    assert run["rewarded"] == {"shown": True, "reward": {"type": "coins", "amount": 10}}
    assert [e[2] for e in run["events"] if e[1] == 2] == ["opened", "impression", "earned_reward", "closed"]
    assert [e[3] for e in run["events"] if e[2] == "earned_reward"] == [{"type": "coins", "amount": 10}]


def test_destroy_and_errors(run):
    assert run["destroyed"]["error"] == "not_loaded"
    assert run["badType"] == "unknown ad type nope"
    assert run["badMethod"] == "GoogleMobileAds has no method nope"
    assert run["consent"]["canRequestAds"] is True


def test_banner_view_sizes_and_events(run):
    names = [e[0] for e in run["banner"]]
    assert names == ["size", "load", "impression"]   # once: an unchanged update does not reload
    assert run["banner"][0][1] == {"width": 360, "height": 54}


def test_native_view(run):
    assert [e[0] for e in run["native"]] == ["size", "load", "impression"]
    assert run["native"][0][1] == {"width": 360, "height": 96}


def test_banner_size_rules(run):
    assert run["sizes"] == [[320, 50], [300, 250], None, [360, 54], [1000, 90], [360, 60], [250, 120]]
    assert run["css"] == ["rgba(17,34,51,1)", "rgba(0,0,0,1)", "x"]


def test_page_loads_it():
    from pyxpo.renderers.web import page_js
    assert str(page_js({"GoogleMobileAds": "pyxpo_google_mobile_ads"})["GoogleMobileAds"]) == WEB_JS
