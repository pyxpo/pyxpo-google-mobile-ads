"""pyxpo_google_mobile_ads native sources answer the whole wire contract Python uses (Kotlin, Swift, web.js), and the iOS module
compiles to an `available: false` stub where the SDK is not linked (Mac Catalyst)."""
import json
import re
from pathlib import Path

import pytest

import pyxpo_google_mobile_ads

ROOT = Path(pyxpo_google_mobile_ads.__path__[0])
KT = ROOT / "android/src/main/java/dev/pyxpo/modules/googlemobileads"
METHODS = ["initialize", "setRequestConfiguration", "requestTracking", "trackingStatus", "openAdInspector",
           "consentRequestInfo", "consentShowFormIfRequired", "consentShowPrivacyOptionsForm", "consentReset",
           "load", "show", "destroy"]
VIEWS = ["PxBannerAd", "PxNativeAd"]
VIEW_EVENTS = ["size", "load", "error", "open", "close", "click", "impression", "paid"]
AD_EVENTS = ["opened", "impression", "clicked", "show_failed", "closed", "earned_reward", "paid"]


def sources():
    return {
        "kotlin": "\n".join(p.read_text() for p in sorted(KT.glob("*.kt"))),
        "swift": (ROOT / "ios/GoogleMobileAdsModule.swift").read_text(),
        "web": (ROOT / "web.js").read_text(),
    }


def test_python_calls_only_contract_methods():
    py = (ROOT / "__init__.py").read_text()
    called = set(re.findall(r'call\(NATIVE, "(\w+)"', py))
    assert called and called <= set(METHODS)


@pytest.mark.parametrize("lang", ["kotlin", "swift", "web"])
def test_every_method_and_view(lang):
    src = sources()[lang]
    # App Tracking Transparency is iOS only: Python never sends the tracking calls to Android
    for m in [m for m in METHODS if lang != "kotlin" or "racking" not in m]:
        assert '"%s"' % m in src, "%s has no %s" % (lang, m)
    for v in VIEWS:
        assert '"%s"' % v in src
    for e in ["interstitial", "rewarded", "rewarded_interstitial", "app_open"]:
        assert '"%s"' % e in src
    assert "GoogleMobileAds.event" in src


@pytest.mark.parametrize("lang", ["kotlin", "swift"])
def test_native_events(lang):
    src = sources()[lang]
    for e in AD_EVENTS:
        assert '"%s"' % e in src, "%s never sends %s" % (lang, e)
    for e in VIEW_EVENTS:
        assert '"%s"' % e in src, "%s views never send %s" % (lang, e)


@pytest.mark.parametrize("lang", ["kotlin", "swift"])
def test_error_codes_python_knows(lang):
    src = sources()[lang]
    for code in re.findall(r'"(no_fill|network|timeout|invalid_request|app_id_missing|already_used|not_ready|'
                           r'not_loaded|app_not_foreground)"', src):
        assert "`%s`" % code in pyxpo_google_mobile_ads.AdError.__doc__


def test_swift_guards_the_sdk():
    s = sources()["swift"]
    guard = "#if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)"
    assert s.startswith("import UIKit\n" + guard)
    assert '["available": false]' in s and '["available": true]' in s
    # everything that names an SDK type sits inside a guarded block
    depth, outside = 0, []
    for line in s.splitlines():
        t = line.strip()
        if t.startswith("#if"):
            depth += 1
        elif t.startswith("#endif"):
            depth -= 1
        elif depth == 0 and re.search(r"\b(BannerView|InterstitialAd|RewardedAd|MobileAds|ConsentInformation|"
                                      r"NativeAdView|AdLoader|ATTrackingManager)\b", t):
            outside.append(t)
    assert depth == 0 and outside == []


def test_module_json_names_the_classes():
    j = json.loads((ROOT / "pyxpo-module.json").read_text())
    assert j["native"] == "GoogleMobileAds"
    assert "class GoogleMobileAdsModule" in sources()["swift"] and j["ios"]["classes"] == ["GoogleMobileAdsModule"]
    for cls in j["android"]["classes"]:
        name = cls.rsplit(".", 1)[1]
        assert re.search(r"\bclass %s\b" % name, sources()["kotlin"])
    manifest = (ROOT / "android/src/main/AndroidManifest.xml").read_text()
    assert "com.google.android.gms.ads.APPLICATION_ID" in manifest and "3940256099942544" in manifest
