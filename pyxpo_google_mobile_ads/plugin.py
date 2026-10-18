"""Config plugin for the AdMob app IDs and the platform keys the Google Mobile Ads SDK reads at startup.

    [tool.pyxpo]
    modules = ["google-mobile-ads"]
    plugins = [["pyxpo_google_mobile_ads.plugin", {"androidAppId": "ca-app-pub-XXXXXXXXXXXXXXXX~YYYYYYYYYY",
                                     "iosAppId": "ca-app-pub-XXXXXXXXXXXXXXXX~ZZZZZZZZZZ"}]]

Options:
- `androidAppId`, `iosAppId`: the AdMob app IDs (AdMob > Apps > App settings). Default: Google's test app IDs,
  which serve test ads only. App IDs are not secret, but keep them in pyproject.toml rather than in code.
- `userTrackingUsageDescription`: iOS `NSUserTrackingUsageDescription`, needed before `request_tracking_async()`.
  Leave it out in apps for children (Apple's Kids Category does not allow tracking).
- `skAdNetworkItems`: SKAdNetwork IDs for iOS install attribution (`SKAdNetworkItems`). Default: Google's own
  (`cstr6suwn9.skadnetwork`); add the ones of your mediation partners.
- `delayAppMeasurementInit`: True keeps Google's app measurement off until the SDK is initialized.
- `removeAdIdPermission`: True removes the Android `AD_ID` permission the SDK adds. Google Play's Families
  policy wants this in apps for children.

Android also gets the app ID in the manifest with `tools:replace`, over the test app ID the module itself declares
(without one the SDK stops the app at launch).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from pyxpo.app_config import ConfigError
from pyxpo.config_plugins import PluginConfig, android_name, tools_name, with_android_manifest, with_info_plist

TEST_ANDROID_APP_ID = "ca-app-pub-3940256099942544~3347511713"
TEST_IOS_APP_ID = "ca-app-pub-3940256099942544~1458002511"
APP_ID_RE = re.compile(r"^ca-app-pub-\d{16}~\d{10}$")
SKAN_RE = re.compile(r"^[a-z0-9]{10}\.skadnetwork$")
GOOGLE_SKAN = "cstr6suwn9.skadnetwork"
APP_ID_KEY = "com.google.android.gms.ads.APPLICATION_ID"
DELAY_KEY = "com.google.android.gms.ads.DELAY_APP_MEASUREMENT_INIT"
AD_ID = "com.google.android.gms.permission.AD_ID"
OPTIONS = ("androidAppId", "iosAppId", "userTrackingUsageDescription", "skAdNetworkItems", "delayAppMeasurementInit",
           "removeAdIdPermission")


def _fail(msg: str) -> ConfigError:
    return ConfigError("pyxpo_google_mobile_ads.plugin: " + msg)


def _app_id(props: Dict[str, Any], key: str, default: str) -> str:
    v = props.get(key, default)
    if not isinstance(v, str) or not APP_ID_RE.match(v):
        raise _fail("%s must be an AdMob app ID like ca-app-pub-0000000000000000~0000000000 (with '~', not the "
                    "'/' of an ad unit ID), got %r" % (key, v))
    return v


def _flag(props: Dict[str, Any], key: str) -> bool:
    v = props.get(key, False)
    if not isinstance(v, bool):
        raise _fail("%s must be true or false, got %r" % (key, v))
    return v


def _skan(props: Dict[str, Any]) -> List[str]:
    items = props.get("skAdNetworkItems", [GOOGLE_SKAN])
    if not isinstance(items, list) or not all(isinstance(i, str) and SKAN_RE.match(i.lower()) for i in items):
        raise _fail("skAdNetworkItems must be a list of IDs like %r" % GOOGLE_SKAN)
    out: List[str] = []
    for i in items:
        if i.lower() not in out:
            out.append(i.lower())
    return out


def _meta(app: ET.Element, name: str, value: str) -> None:
    for el in app.findall("meta-data"):
        if el.get(android_name("name")) == name:
            app.remove(el)
    ET.SubElement(app, "meta-data", {android_name("name"): name, android_name("value"): value,
                                     tools_name("replace"): "android:value"})


def plugin(config: PluginConfig, props: Dict[str, Any]) -> PluginConfig:
    if not isinstance(props, dict):
        raise _fail("options must be a table")
    unknown = set(props) - set(OPTIONS)
    if unknown:
        raise _fail("unknown option(s) %s (known: %s)" % (", ".join(sorted(unknown)), ", ".join(OPTIONS)))
    android_id = _app_id(props, "androidAppId", TEST_ANDROID_APP_ID)
    ios_id = _app_id(props, "iosAppId", TEST_IOS_APP_ID)
    tracking = props.get("userTrackingUsageDescription")
    if tracking is not None and (not isinstance(tracking, str) or not tracking.strip()):
        raise _fail("userTrackingUsageDescription must be a non-empty string")
    skan = _skan(props)
    delay = _flag(props, "delayAppMeasurementInit")
    no_ad_id = _flag(props, "removeAdIdPermission")

    def info_plist(d: Dict[str, Any]) -> None:
        d["GADApplicationIdentifier"] = ios_id
        have = [x.get("SKAdNetworkIdentifier", "").lower() for x in d.get("SKAdNetworkItems") or [] if isinstance(x, dict)]
        d["SKAdNetworkItems"] = list(d.get("SKAdNetworkItems") or []) + [
            {"SKAdNetworkIdentifier": i} for i in skan if i not in have]
        if tracking is not None:
            d["NSUserTrackingUsageDescription"] = tracking
        if delay:
            d["GADDelayAppMeasurementInit"] = True

    def manifest(root: ET.Element) -> None:
        app = root.find("application")
        if app is None:
            raise _fail("the Android manifest has no <application>")
        _meta(app, APP_ID_KEY, android_id)
        if delay:
            _meta(app, DELAY_KEY, "true")
        if no_ad_id:
            for el in root.findall("uses-permission"):
                if el.get(android_name("name")) == AD_ID:
                    root.remove(el)
            root.insert(0, ET.Element("uses-permission", {android_name("name"): AD_ID, tools_name("node"): "remove"}))

    with_info_plist(config, info_plist)
    with_android_manifest(config, manifest)
    return config
