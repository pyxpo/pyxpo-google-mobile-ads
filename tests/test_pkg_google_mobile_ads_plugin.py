"""pyxpo_google_mobile_ads.plugin: AdMob app IDs in the Android manifest and Info.plist, SKAdNetwork, tracking text, AD_ID."""
import xml.etree.ElementTree as ET

import pytest

from pyxpo.app_config import ConfigError
from pyxpo.config_plugins import ANDROID_NS, TOOLS_NS, Mods, PluginConfig

from pyxpo_google_mobile_ads.plugin import TEST_ANDROID_APP_ID, TEST_IOS_APP_ID, plugin

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" xmlns:tools="http://schemas.android.com/tools">
    <uses-permission android:name="android.permission.INTERNET" />
    <application android:allowBackup="false" tools:replace="android:allowBackup">
        <meta-data android:name="com.google.android.gms.ads.APPLICATION_ID" android:value="old" />
        <activity android:name="dev.pyxpo.runtime.MainActivity" />
    </application>
</manifest>
"""
A = "{%s}" % ANDROID_NS
T = "{%s}" % TOOLS_NS
ANDROID_ID = "ca-app-pub-1234567890123456~1234567890"
IOS_ID = "ca-app-pub-1234567890123456~0987654321"


def run(tmp_path, **props):
    mods = Mods()
    plugin(PluginConfig({"android": {"package": "com.example.app"}}, tmp_path, mods), props)
    return mods.apply_info_plist({}), ET.fromstring(mods.apply_manifest(MANIFEST))


def metas(root):
    return {m.get(A + "name"): m for m in root.find("application").findall("meta-data")}


def test_defaults_are_google_test_app_ids(tmp_path):
    plist, root = run(tmp_path)
    assert plist == {"GADApplicationIdentifier": TEST_IOS_APP_ID,
                     "SKAdNetworkItems": [{"SKAdNetworkIdentifier": "cstr6suwn9.skadnetwork"}]}
    m = metas(root)
    assert list(m) == ["com.google.android.gms.ads.APPLICATION_ID"]  # the old entry is replaced, not doubled
    app_id = m["com.google.android.gms.ads.APPLICATION_ID"]
    assert app_id.get(A + "value") == TEST_ANDROID_APP_ID
    assert app_id.get(T + "replace") == "android:value"  # wins over the module's own test ID
    assert root.find("application/activity") is not None


def test_real_ids_and_options(tmp_path):
    plist, root = run(tmp_path, androidAppId=ANDROID_ID, iosAppId=IOS_ID, delayAppMeasurementInit=True,
                      userTrackingUsageDescription="Used to show ads that fit you.",
                      skAdNetworkItems=["CSTR6SUWN9.skadnetwork", "4fzdc2evr5.skadnetwork", "cstr6suwn9.skadnetwork"],
                      removeAdIdPermission=True)
    assert plist["GADApplicationIdentifier"] == IOS_ID
    assert plist["NSUserTrackingUsageDescription"] == "Used to show ads that fit you."
    assert plist["GADDelayAppMeasurementInit"] is True
    assert [x["SKAdNetworkIdentifier"] for x in plist["SKAdNetworkItems"]] == [
        "cstr6suwn9.skadnetwork", "4fzdc2evr5.skadnetwork"]
    m = metas(root)
    assert m["com.google.android.gms.ads.APPLICATION_ID"].get(A + "value") == ANDROID_ID
    assert m["com.google.android.gms.ads.DELAY_APP_MEASUREMENT_INIT"].get(A + "value") == "true"
    perms = {p.get(A + "name"): p for p in root.findall("uses-permission")}
    assert perms["com.google.android.gms.permission.AD_ID"].get(T + "node") == "remove"
    assert "android.permission.INTERNET" in perms


def test_existing_skadnetwork_items_are_kept(tmp_path):
    mods = Mods()
    plugin(PluginConfig({}, tmp_path, mods), {})
    plist = mods.apply_info_plist({"SKAdNetworkItems": [{"SKAdNetworkIdentifier": "CSTR6SUWN9.skadnetwork"},
                                                        {"SKAdNetworkIdentifier": "v9wttpbfk9.skadnetwork"}]})
    assert len(plist["SKAdNetworkItems"]) == 2


def test_no_tracking_text_unless_given(tmp_path):
    plist, _ = run(tmp_path, iosAppId=IOS_ID)
    assert "NSUserTrackingUsageDescription" not in plist
    assert "GADDelayAppMeasurementInit" not in plist


@pytest.mark.parametrize("props,msg", [
    ({"androidAppId": "ca-app-pub-1234567890123456/1234567890"}, "with '~'"),  # an ad unit ID, not an app ID
    ({"iosAppId": "ca-app-pub-123~456"}, "iosAppId"),
    ({"iosAppId": 5}, "iosAppId"),
    ({"skAdNetworkItems": "cstr6suwn9.skadnetwork"}, "skAdNetworkItems"),
    ({"skAdNetworkItems": ["nope"]}, "skAdNetworkItems"),
    ({"userTrackingUsageDescription": "  "}, "userTrackingUsageDescription"),
    ({"delayAppMeasurementInit": "yes"}, "true or false"),
    ({"removeAdIdPermission": 1}, "true or false"),
    ({"appId": ANDROID_ID}, "unknown option"),
])
def test_bad_options(tmp_path, props, msg):
    with pytest.raises(ConfigError, match=msg):
        run(tmp_path, **props)


def test_manifest_without_application(tmp_path):
    mods = Mods()
    plugin(PluginConfig({}, tmp_path, mods), {})
    with pytest.raises(ConfigError, match="no <application>"):
        mods.apply_manifest('<manifest xmlns:android="%s"/>' % ANDROID_NS)
