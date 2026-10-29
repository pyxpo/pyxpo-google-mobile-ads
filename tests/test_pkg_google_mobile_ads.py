"""pyxpo-google-mobile-ads: request config, consent, full-screen ad state machine and events, hooks, banner/native views."""
import asyncio
import json
from pathlib import Path

import pytest

from pyxpo.module_api import dispatch
from pyxpo.module_testing import FakeBridge

import pyxpo_google_mobile_ads as Ads

PKG = Path(Ads.__file__).parent
ON = {"GoogleMobileAds": {"available": True}}
REAL = "ca-app-pub-1234567890123456/1234567890"


@pytest.fixture(autouse=True)
def fresh():
    saved = dict(Ads._config)
    Ads._state.update(initialized=True, init={})     # most tests: the app has started the SDK
    Ads._on_ready.clear()
    Ads._ready_waiters.clear()
    Ads._showing_any[0] = None
    Ads._live.clear()
    Ads.consent._info = {"status": "unknown", "can_request_ads": False, "privacy_options_required": False,
                         "form_available": False}
    yield
    Ads._config.clear()
    Ads._config.update(saved)
    Ads._config.update(test_device_ids=[], tag_for_child_directed_treatment=None,
                       tag_for_under_age_of_consent=None, max_ad_content_rating=None)


def run(coro):
    return asyncio.run(coro)


def event(ad, name, data=None):
    dispatch({"name": "GoogleMobileAds.event", "data": {"id": ad._id, "event": name, "data": data}})


def native(fb, show=None, load=None):
    fb.on("GoogleMobileAds", "load", load or (lambda a: {"ok": True}))
    fb.on("GoogleMobileAds", "show", show or (lambda a: {"shown": True}))
    fb.on("GoogleMobileAds", "destroy", lambda a: None)
    return fb


# -- ids, config ---------------------------------------------------------------------------------------------

def test_test_ids_follow_platform():
    with FakeBridge("ios"):
        assert Ads.TestIds.INTERSTITIAL == "ca-app-pub-3940256099942544/4411468910"
        assert Ads.TestIds.APP_ID.endswith("~1458002511")
    with FakeBridge("android"):
        assert Ads.TestIds.INTERSTITIAL == "ca-app-pub-3940256099942544/1033173712"
        assert Ads.TestIds.REWARDED == "ca-app-pub-3940256099942544/5224354917"
    with FakeBridge("desktop"):
        assert Ads.TestIds.BANNER == "ca-app-pub-3940256099942544/6300978111"
        with pytest.raises(AttributeError):
            Ads.TestIds.NOPE
    assert Ads.is_test_id("ca-app-pub-3940256099942544/2435281174") and not Ads.is_test_id(REAL)
    assert not Ads.is_test_id("ca-app-pub-3940256099942544~3347511713")  # an app ID is not a unit


def test_manifest_valid():
    from pyxpo.build import autolink
    meta = autolink.validate(json.loads((PKG / "pyxpo-module.json").read_text()), "google-mobile-ads")
    assert meta["native"] == "GoogleMobileAds" and meta["name"] == "google-mobile-ads" and meta["name"] in autolink.OFFICIAL
    assert {p["name"] for p in meta["ios"]["swiftPackages"]} == {"GoogleMobileAds", "GoogleUserMessagingPlatform"}
    assert all(p["platformFilter"] == "iOS" for p in meta["ios"]["swiftPackages"])  # kept out of Mac Catalyst
    assert any(d.startswith("com.google.android.gms:play-services-ads:") for d in meta["android"]["dependencies"])


def test_request_configuration_and_npa():
    Ads.set_request_configuration(tag_for_child_directed_treatment=True, max_ad_content_rating="G",
                                  test_device_ids=["ABC"])
    Ads.set_request_configuration(max_ad_content_rating=None)  # None keeps the value
    assert Ads.get_request_configuration() == {"test_device_ids": ["ABC"], "tag_for_child_directed_treatment": True,
                                               "tag_for_under_age_of_consent": None, "max_ad_content_rating": "G"}
    assert Ads._config_wire() == {"testDeviceIds": ["ABC"], "tagForChildDirectedTreatment": True,
                                  "tagForUnderAgeOfConsent": None, "maxAdContentRating": "G"}
    # child-directed forces non-personalized even when asked otherwise
    assert Ads._request_wire({"non_personalized": False})["nonPersonalized"] is True
    Ads.set_request_configuration(tag_for_child_directed_treatment=False)
    assert Ads._request_wire({"keywords": ["art"], "content_url": "https://x.dev/a"}) == {
        "keywords": ["art"], "contentUrl": "https://x.dev/a", "neighboringContentUrls": [], "nonPersonalized": False}
    for bad in ({"max_ad_content_rating": "R"}, {"tag_for_child_directed_treatment": 1},
                {"test_device_ids": [""]}):
        with pytest.raises(ValueError):
            Ads.set_request_configuration(**bad)
    for bad in ({"keyword": []}, {"keywords": "art"}, {"content_url": "javascript:x"},
                {"neighboring_content_urls": ["ftp://a"]}, {"non_personalized": "yes"}):
        with pytest.raises(ValueError):
            Ads._request_wire(bad)


def not_started():
    Ads._state.update(initialized=False, init=None)


def test_config_applied_live_after_initialize():
    not_started()
    with FakeBridge("android", constants=ON) as fb:
        fb.on("GoogleMobileAds", "initialize", lambda cfg: {"com.google.ads.mediation.AdMob": {"state": "ready"}})
        fb.on("GoogleMobileAds", "setRequestConfiguration", lambda cfg: None)
        Ads.set_request_configuration(max_ad_content_rating="PG")
        assert fb.calls == []                       # not started yet: kept for initialize
        res = run(Ads.initialize_async())
        assert res["com.google.ads.mediation.AdMob"]["state"] == "ready"
        assert run(Ads.initialize_async()) == res   # once
        Ads.set_request_configuration(max_ad_content_rating="G")
        assert [c[1] for c in fb.calls] == ["initialize", "setRequestConfiguration"]
        assert fb.calls[0][2][0]["maxAdContentRating"] == "PG" and fb.calls[1][2][0]["maxAdContentRating"] == "G"


def test_unit_id_checked():
    for bad in ("", "abc", "ca-app-pub-3940256099942544~3347511713", None, 5):
        with pytest.raises(ValueError):
            Ads.InterstitialAd(bad)
    with pytest.raises(ValueError):
        Ads.InterstitialAd(REAL, {"bogus": 1})
    with pytest.raises(ValueError):
        Ads.RewardedAd(REAL, user_id=3)


# -- availability ------------------------------------------------------------------------------------------

def test_unavailable_native_carries_on():
    """Not linked, or Mac Catalyst (native says available False): loads fail as unavailable, show is not shown."""
    not_started()                      # and they fail at once, not waiting for an initialize that does nothing
    for consts in ({}, {"GoogleMobileAds": {"available": False}}):
        with FakeBridge("ios", constants=consts) as fb:
            assert not Ads.is_available()
            assert run(Ads.initialize_async()) == {}
            ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
            failed = []
            ad.add_listener("failed", failed.append)
            with pytest.raises(Ads.AdError) as e:
                run(ad.load_async())
            assert e.value.code == "unavailable" and failed == [e.value] and ad.error is e.value
            res = run(ad.show_async())
            assert not res.shown and res.error.code == "not_loaded"
            assert run(Ads.consent.request_info_async())["can_request_ads"] is False
            assert run(Ads.request_tracking_async()) == "unavailable"
            assert fb.calls == []


def test_desktop_fills_only_test_units():
    not_started()
    with FakeBridge("desktop") as fb:
        native(fb)
        assert Ads.is_available()
        assert "simulated" in run(Ads.initialize_async())
        with pytest.raises(Ads.AdError) as e:
            run(Ads.InterstitialAd(REAL).load_async())
        assert e.value.code == "unavailable" and fb.calls == []
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        run(ad.load_async())
        assert ad.loaded and fb.calls[0][1] == "load"
        assert run(Ads.consent.request_info_async()) == {"status": "not_required", "can_request_ads": True,
                                                         "privacy_options_required": False, "form_available": False}


def test_desktop_without_page_handler_is_unavailable():
    with FakeBridge("desktop"):        # no handler: the call raises, like a window without the module's web.js
        with pytest.raises(Ads.AdError) as e:
            run(Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL).load_async())
        assert e.value.code == "unavailable"


# -- full-screen state machine ------------------------------------------------------------------------------

def test_load_show_close_and_reload():
    with FakeBridge("android", constants=ON) as fb:
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL, {"keywords": ["draw"]})
        seen = []
        for ev in ("loaded", "opened", "impression", "clicked", "closed", "paid"):
            ad.add_listener(ev, lambda d, ev=ev: seen.append((ev, d)))

        def show(a):
            event(ad, "opened")
            event(ad, "impression")
            event(ad, "clicked")
            event(ad, "paid", {"valueMicros": 1500, "currencyCode": "USD", "precision": "estimated"})
            event(ad, "closed")
            return {"shown": True}

        native(fb, show=show)
        run(ad.load_async())
        first = ad._id
        assert ad.loaded and not ad.loading and repr(ad).endswith("loaded)")
        run(ad.load_async())                        # loaded: no second load
        assert [c[1] for c in fb.calls] == ["load"]
        assert fb.calls[0][2][0] == {"id": first, "type": "interstitial", "unitId": Ads.TestIds.INTERSTITIAL,
                                     "request": {"keywords": ["draw"], "contentUrl": None,
                                                 "neighboringContentUrls": [], "nonPersonalized": False}}
        res = run(ad.show_async())
        assert res.shown and res.error is None and res.reward is None
        assert [e for e, _ in seen] == ["loaded", "opened", "impression", "clicked", "paid", "closed"]
        assert seen[4][1] == {"value_micros": 1500, "currency_code": "USD", "precision": "estimated"}
        assert fb.calls[1] == ("GoogleMobileAds", "show", ({"id": first, "immersive": False},))
        assert not ad.loaded and ad._id == 0 and first not in Ads._live
        r2 = run(ad.show_async())                   # one show per load
        assert not r2.shown and r2.error.code == "not_loaded"
        run(ad.load_async())
        assert ad._id != first and ad.loaded


def test_load_failure_and_retry():
    with FakeBridge("android", constants=ON) as fb:
        results = [{"ok": False, "error": "no_fill", "message": "No fill.", "code": 3, "domain": "com.google.android.gms.ads"},
                   {"ok": True}]
        native(fb, load=lambda a: results.pop(0))
        ad = Ads.RewardedAd(Ads.TestIds.REWARDED)
        with pytest.raises(Ads.AdError) as e:
            run(ad.load_async())
        err = e.value
        assert (err.code, err.message, err.sdk_code, err.domain) == ("no_fill", "No fill.", 3,
                                                                    "com.google.android.gms.ads")
        assert ad.error is err and not ad.loaded and not ad.loading and ad._id == 0 and Ads._live == {}
        run(ad.load_async())
        assert ad.loaded and ad.error is None


def hold(fb, method, when=lambda args: True):
    """Makes `method` calls (that match `when`) reply only when the returned future is resolved."""
    import pyxpo.apis._bridge as br
    gate = asyncio.get_running_loop().create_future()
    orig = br._app.call

    def call(module, m, *args, reply=False):
        if m == method and when(args):
            fb.calls.append((module, m, args))
            res = fb._handlers[(module, m)](*args)

            async def later():
                await gate
                return res

            return later()
        return orig(module, m, *args, reply=reply)

    br._app.call = call
    return gate


def test_concurrent_loads_share_one_call():
    async def go(fb):
        gate = hold(fb, "load")
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        t1 = asyncio.ensure_future(ad.load_async())
        t2 = asyncio.ensure_future(ad.load_async())
        await asyncio.sleep(0)
        assert ad.loading and not ad.loaded
        gate.set_result(None)
        await asyncio.gather(t1, t2)
        return ad

    with FakeBridge("android", constants=ON) as fb:
        native(fb)
        ad = run(go(fb))
        assert ad.loaded and [c[1] for c in fb.calls] == ["load"]


def test_concurrent_load_failure_reaches_both():
    async def go(fb):
        gate = hold(fb, "load")
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        t1 = asyncio.ensure_future(ad.load_async())
        t2 = asyncio.ensure_future(ad.load_async())
        await asyncio.sleep(0)
        gate.set_result(None)
        return await asyncio.gather(t1, t2, return_exceptions=True)

    with FakeBridge("android", constants=ON) as fb:
        native(fb, load=lambda a: {"ok": False, "error": "no_fill"})
        r1, r2 = run(go(fb))
        assert isinstance(r1, Ads.AdError) and r1 is r2 and r1.code == "no_fill"


def test_show_guards():
    a_ids = []

    async def go(fb):
        a = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        b = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        await a.load_async()
        await b.load_async()
        a_ids.append(a._id)
        gate = hold(fb, "show", lambda args: args[0]["id"] == a._id)
        t = asyncio.ensure_future(a.show_async())
        await asyncio.sleep(0)
        assert a.showing and not a.loaded
        again = await a.show_async()
        other = await b.show_async()
        with pytest.raises(Ads.AdError) as e:
            await a.load_async()
        assert e.value.code == "busy"
        gate.set_result(None)
        return await t, again, other, b

    with FakeBridge("android", constants=ON) as fb:
        native(fb)
        res, again, other, b = run(go(fb))
        assert res.shown
        assert again.error.code == "already_showing"
        assert other.error.code == "already_showing" and b.loaded  # b keeps its ad for later
        assert Ads._showing_any[0] is None
        assert [c for c in fb.calls if c[1] == "show"] == [("GoogleMobileAds", "show", ({"id": a_ids[0], "immersive": False},))]


def test_expiry():
    with FakeBridge("android", constants=ON) as fb:
        native(fb)
        now = [1000.0]
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        ad.clock = lambda: now[0]
        run(ad.load_async())
        loaded_id = ad._id
        now[0] += 3601
        assert not ad.loaded
        res = run(ad.show_async())
        assert not res.shown and res.error.code == "expired"
        assert ("GoogleMobileAds", "destroy", ({"id": loaded_id},)) in fb.calls
        app_open = Ads.AppOpenAd(Ads.TestIds.APP_OPEN)
        app_open.clock = lambda: now[0]
        run(app_open.load_async())
        now[0] += 3 * 3600
        assert app_open.loaded                      # app open ads keep for 4 hours
        now[0] += 3601
        assert not app_open.loaded
        run(app_open.load_async())                  # an expired ad loads again
        assert app_open.loaded


def test_rewarded_reward_from_event_and_reply():
    with FakeBridge("ios", constants=ON) as fb:
        ad = Ads.RewardedAd(Ads.TestIds.REWARDED, user_id="u1", custom_data="c")
        got = []
        ad.add_listener("earned_reward", got.append)

        def show(a):
            event(ad, "opened")
            event(ad, "earned_reward", {"type": "coins", "amount": 10})
            return {"shown": True}

        native(fb, show=show)
        run(ad.load_async())
        assert fb.calls[0][2][0]["serverSideVerification"] == {"userId": "u1", "customData": "c"}
        assert fb.calls[0][2][0]["type"] == "rewarded"
        res = run(ad.show_async())
        assert res.shown and res.reward == Ads.Reward("coins", 10) and got == [Ads.Reward("coins", 10)]
        # reward only in the reply (no event), and an interstitial has no reward event
        fb._handlers[("GoogleMobileAds", "show")] = lambda a: {"shown": True, "reward": {"type": "gem", "amount": 1}}
        run(ad.load_async())
        assert run(ad.show_async()).reward == Ads.Reward("gem", 1)
        with pytest.raises(ValueError):
            Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL).add_listener("earned_reward", print)
        with pytest.raises(ValueError):
            ad.add_listener("bogus", print)
        assert Ads.RewardedInterstitialAd(Ads.TestIds.REWARDED_INTERSTITIAL).TYPE == "rewarded_interstitial"


def test_show_failure_is_a_result():
    with FakeBridge("android", constants=ON) as fb:
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL, immersive=True)
        fails = []
        ad.add_listener("show_failed", fails.append)

        def show(a):
            event(ad, "show_failed", {"error": "internal", "message": "The ad has already been shown.", "code": 1})
            return {"shown": False, "error": "internal", "message": "The ad has already been shown.", "code": 1}

        native(fb, show=show)
        run(ad.load_async())
        res = run(ad.show_async())
        assert not res.shown and res.error.code == "internal" and res.error.sdk_code == 1
        assert fails and fails[0].message.startswith("The ad")
        assert fb.calls[1][2][0]["immersive"] is True

        def boom(a):
            raise RuntimeError("bridge gone")

        fb._handlers[("GoogleMobileAds", "show")] = boom
        run(ad.load_async())
        res = run(ad.show_async())
        assert not res.shown and res.error.code == "internal" and "bridge gone" in res.error.message
        assert not ad.showing and Ads._showing_any[0] is None


def test_listener_error_is_reported_and_remove():
    with FakeBridge("android", constants=ON) as fb:
        native(fb)
        ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        reported, seen = [], []
        import pyxpo.apis._bridge as br
        br._app.on_error = reported.append

        def bad(d):
            raise ValueError("listener bug")

        ad.add_listener("loaded", bad)
        sub = ad.add_listener("loaded", seen.append)
        run(ad.load_async())
        assert len(reported) == 1 and seen == [None]
        sub.remove()
        sub.remove()                                # twice is fine
        event(ad, "closed")                         # routed by id
        dispatch({"name": "GoogleMobileAds.event", "data": {"id": 999999, "event": "closed"}})  # unknown id: ignored
        dispatch({"name": "GoogleMobileAds.event", "data": "junk"})
        ad.destroy()
        assert ("GoogleMobileAds", "destroy", ({"id": fb.calls[0][2][0]["id"]},)) in fb.calls and not ad.loaded


def test_events_for_other_ads_do_not_leak():
    with FakeBridge("android", constants=ON) as fb:
        native(fb)
        a, b = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL), Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
        hits = []
        a.add_listener("clicked", lambda d: hits.append("a"))
        b.add_listener("clicked", lambda d: hits.append("b"))
        run(a.load_async())
        run(b.load_async())
        event(b, "clicked")
        assert hits == ["b"]


# -- consent, tracking ---------------------------------------------------------------------------------------

def test_consent_flow():
    not_started()
    with FakeBridge("android", constants=ON) as fb:
        fb.on("GoogleMobileAds", "consentRequestInfo", lambda a: {"status": "required", "canRequestAds": False,
                                                       "privacyOptionsRequired": False, "formAvailable": True})
        fb.on("GoogleMobileAds", "consentShowFormIfRequired", lambda: {"status": "obtained", "canRequestAds": True,
                                                           "privacyOptionsRequired": True, "formAvailable": True})
        fb.on("GoogleMobileAds", "consentShowPrivacyOptionsForm", lambda: {"error": "internal", "message": "no form"})
        fb.on("GoogleMobileAds", "consentReset", lambda: None)
        Ads.set_request_configuration(tag_for_under_age_of_consent=True, test_device_ids=["T1"])
        info = run(Ads.consent.request_info_async(debug_geography="eea"))
        assert info["status"] == "required" and not Ads.consent.can_request_ads()
        assert fb.calls[0][2][0] == {"debugGeography": "eea", "testDeviceIds": ["T1"], "tagForUnderAgeOfConsent": True}
        info = run(Ads.consent.show_form_if_required_async())
        assert info == Ads.consent.info() and Ads.consent.can_request_ads() and info["privacy_options_required"]
        with pytest.raises(Ads.AdError):
            run(Ads.consent.show_privacy_options_form_async())
        run(Ads.consent.reset_async())
        assert Ads.consent.info()["status"] == "unknown"
        with pytest.raises(ValueError):
            run(Ads.consent.request_info_async(debug_geography="mars"))


def test_tracking_ios_only():
    with FakeBridge("ios", constants=ON) as fb:
        fb.on("GoogleMobileAds", "requestTracking", lambda: "denied")
        fb.on("GoogleMobileAds", "trackingStatus", lambda: "not_determined")
        assert run(Ads.request_tracking_async()) == "denied"
        assert run(Ads.get_tracking_status_async()) == "not_determined"
    with FakeBridge("android", constants=ON) as fb:
        assert run(Ads.request_tracking_async()) == "unavailable" and fb.calls == []


# -- hooks and views -----------------------------------------------------------------------------------------

def _mount(root):
    from pyxpo import LocalChannel, create_app
    from pyxpo.renderers.mock import MockRenderer
    ch = LocalChannel()
    r = MockRenderer(ch)
    return create_app(root, ch), r


class Mounted:
    """Mounts `body` under a FakeBridge: create_app sets up its own platform env, so the body renders only once
    the fake bridge is in place."""

    def __init__(self, body, os_name, constants=None):
        from pyxpo import View, component, use_state
        self.fb = FakeBridge(os_name, constants=constants)
        box = {}

        @component
        def Root():
            on, set_on = use_state(False)
            box["on"] = set_on
            return body() if on else View()

        self.app, self.r = _mount(Root)
        self._box = box

    def __enter__(self):
        self.fb.__enter__()
        self._box["on"](True)
        self.app.flush()
        return self

    def __exit__(self, *exc):
        self.fb.__exit__(*exc)


def test_use_interstitial_ad_preloads_shows_reloads_and_releases():
    from pyxpo import View, component, use_state
    seen, setters = [], {}

    @component
    def Screen():
        h = Ads.use_interstitial_ad(Ads.TestIds.INTERSTITIAL)
        seen.append(h)
        return View()

    @component
    def Root():
        on, set_on = use_state(True)
        setters["on"] = set_on
        return Screen() if on else View()

    async def go():
        app, _ = _mount(Root)
        with FakeBridge("android", constants=ON) as fb:
            native(fb)
            app.flush()
            for _ in range(3):
                await asyncio.sleep(0)
            app.flush()
            h = seen[-1]
            assert h.loaded and not h.loading and h.error is None
            res = await h.show_async()
            assert res.shown
            for _ in range(3):
                await asyncio.sleep(0)
            app.flush()
            assert seen[-1].loaded                   # reloaded after the close
            assert [c[1] for c in fb.calls] == ["load", "show", "load"]
            setters["on"](False)
            app.flush()
            assert fb.calls[-1][1] == "destroy"      # released on unmount
            assert Ads._live == {}

    # the app mounts before the bridge is faked, so the first render has no ad state yet
    run(go())


def test_use_rewarded_ad_load_error_shows_in_handle():
    from pyxpo import View, component
    seen = []

    @component
    def Screen():
        seen.append(Ads.use_rewarded_ad(Ads.TestIds.REWARDED))
        return View()

    async def go():
        app, _ = _mount(Screen)
        with FakeBridge("android", constants=ON) as fb:
            native(fb, load=lambda a: {"ok": False, "error": "network", "message": "offline"})
            app.flush()
            for _ in range(3):
                await asyncio.sleep(0)
            app.flush()
            assert seen[-1].error.code == "network" and not seen[-1].loaded

    run(go())


def test_banner_sizes_checked():
    for bad in ("HUGE", "10x10", "320x", "9999x50"):
        with pytest.raises(ValueError):
            Ads._banner_size(bad)
    assert Ads._banner_size("320x60") == (320, 60) and Ads._banner_size("ANCHORED_ADAPTIVE_BANNER") is None
    assert Ads._banner_size("LEADERBOARD") == (728, 90)


def test_adaptive_banner_grows_from_size_event_and_maps_events():
    from pyxpo import View, component
    got = []

    def body():
        return View(Ads.BannerAd(Ads.TestIds.ADAPTIVE_BANNER, on_error=got.append, on_load=got.append,
                                 on_paid=got.append))

    with Mounted(body, "android", ON) as m:
        app, r = m.app, m.r
        (node,) = r.find_all("PxBannerAd")
        p = node.props
        assert p["unit_id"] == Ads.TestIds.ADAPTIVE_BANNER and p["size"] == "ANCHORED_ADAPTIVE_BANNER"
        assert p["request"]["nonPersonalized"] is False and p["on_load"] is True and p["on_size"] is True
        assert "on_click" not in p
        assert r.resolved(node, "height") == 0      # nothing shows until an ad fills
        r.emit("size", node, {"value": {"width": 360, "height": 56.4}})
        app.flush()
        assert r.resolved(r.find_all("PxBannerAd")[0], "height") == 56
        r.emit("size", node, {"value": {"height": float("nan")}})   # junk is ignored
        app.flush()
        assert r.resolved(r.find_all("PxBannerAd")[0], "height") == 56
        r.emit("load", node, {"value": {"width": 360, "height": 56}})
        r.emit("error", node, {"value": {"error": "no_fill", "message": "No fill.", "code": 3}})
        r.emit("paid", node, {"value": {"valueMicros": 20, "currencyCode": "EUR", "precision": "precise"}})
        assert got[0] == {"width": 360, "height": 56}
        assert isinstance(got[1], Ads.AdError) and got[1].code == "no_fill" and got[1].sdk_code == 3
        assert got[2] == {"value_micros": 20, "currency_code": "EUR", "precision": "precise"}


def test_fixed_banner_reserves_box():
    from pyxpo import component

    def body():
        return Ads.BannerAd(Ads.TestIds.BANNER, Ads.BannerAdSize.MEDIUM_RECTANGLE)

    with Mounted(body, "ios", ON) as m:
        r = m.r
        (node,) = r.find_all("PxBannerAd")
        assert r.resolved(node, "height") == 250 and r.resolved(node, "width") == 300


def test_views_render_nothing_without_ads():
    from pyxpo import View, component

    def body():
        return View(Ads.BannerAd(Ads.TestIds.BANNER), Ads.NativeAd(Ads.TestIds.NATIVE), Ads.BannerAd(REAL))

    for consts in ({}, {"GoogleMobileAds": {"available": False}}):
        with Mounted(body, "ios", consts) as m:
            assert m.r.find_all("PxBannerAd") == [] and m.r.find_all("PxNativeAd") == []
    with Mounted(body, "desktop") as m:             # simulated: test units only
        r = m.r
        assert len(r.find_all("PxBannerAd")) == 1 and len(r.find_all("PxNativeAd")) == 1


def test_native_ad_props():
    from pyxpo import component

    def body():
        return Ads.NativeAd(Ads.TestIds.NATIVE, "small", background_color="#fff", button_color="#ff0000")

    with Mounted(body, "android", ON) as m:
        app, r = m.app, m.r
        (node,) = r.find_all("PxNativeAd")
        p = node.props
        assert p["template"] == "small" and p["background_color"] == 0xFFFFFFFF and p["button_color"] == 0xFFFF0000
        assert p["text_color"] is None and r.resolved(node, "height") == 0
        r.emit("size", node, {"value": {"height": 96}})
        app.flush()
        assert r.resolved(r.find_all("PxNativeAd")[0], "height") == 96
    with pytest.raises(ValueError):
        _mount_bad()
    for bad in ({"max_height": 5}, {"max_height": True}, {"request_options": {"x": 1}}):
        with pytest.raises(ValueError):
            Ads.BannerAd(Ads.TestIds.BANNER, **bad)
    with pytest.raises(ValueError):
        Ads.BannerAd("nope")


def _mount_bad():
    Ads.NativeAd(Ads.TestIds.NATIVE, "large")     # checked at the call site


def test_load_waits_for_initialize():
    """A load started before initialize_async (a hook's preload on mount) sends nothing until the SDK has started
    with the configuration: the child-directed tag is on the very first request."""
    not_started()

    async def go():
        with FakeBridge("android", constants=ON) as fb:
            native(fb)
            fb.on("GoogleMobileAds", "initialize", lambda cfg: {})
            ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
            task = asyncio.ensure_future(ad.load_async())
            for _ in range(3):
                await asyncio.sleep(0)
            assert ad.loading and fb.calls == []
            Ads.set_request_configuration(tag_for_child_directed_treatment=True)
            await Ads.initialize_async()
            await task
            assert ad.loaded and [c[1] for c in fb.calls] == ["initialize", "load"]
            assert fb.calls[0][2][0]["tagForChildDirectedTreatment"] is True
            assert fb.calls[1][2][0]["request"]["nonPersonalized"] is True
            assert Ads._ready_waiters == []

    run(go())


def test_views_wait_for_initialize_then_render_once():
    """Banners and native ads mount before the app's setup has run consent and initialize: they send no request
    until then (a request made earlier changes once the configuration is in, and the ad loads twice)."""
    not_started()

    def body():
        return View(Ads.BannerAd(Ads.TestIds.ADAPTIVE_BANNER),
                    Ads.BannerAd(Ads.TestIds.BANNER, Ads.BannerAdSize.MEDIUM_RECTANGLE),
                    Ads.NativeAd(Ads.TestIds.NATIVE))

    from pyxpo import View
    with Mounted(body, "android", ON) as m:
        app, r = m.app, m.r
        m.fb.on("GoogleMobileAds", "initialize", lambda cfg: {})
        assert r.find_all("PxBannerAd") == [] and r.find_all("PxNativeAd") == []
        assert len(Ads._on_ready) == 3
        Ads.set_request_configuration(tag_for_child_directed_treatment=True)
        run(Ads.initialize_async())
        app.flush()
        banners, natives = r.find_all("PxBannerAd"), r.find_all("PxNativeAd")
        assert len(banners) == 2 and len(natives) == 1
        assert all(n.props["request"]["nonPersonalized"] is True for n in banners + natives)
        assert r.resolved(banners[1], "height") == 250
        assert Ads._on_ready == []           # each view stopped watching on its re-render's effect cleanup


def test_fixed_banner_keeps_its_box_before_initialize():
    not_started()

    def body():
        return Ads.BannerAd(Ads.TestIds.BANNER, Ads.BannerAdSize.MEDIUM_RECTANGLE)

    with Mounted(body, "ios", ON) as m:
        assert m.r.find_all("PxBannerAd") == []
        (box,) = [n for n in m.r.find_all("View") if m.r.resolved(n, "height") == 250]
        assert m.r.resolved(box, "width") == 300


def test_camel_aliases():
    assert Ads.useInterstitialAd is Ads.use_interstitial_ad and Ads.useRewardedAd is Ads.use_rewarded_ad
