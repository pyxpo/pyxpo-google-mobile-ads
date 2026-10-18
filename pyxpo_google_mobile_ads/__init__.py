"""Google AdMob ads for pyxpo apps: banner, interstitial, rewarded, rewarded interstitial, app open and native ads.

    import pyxpo_google_mobile_ads as Ads

    Ads.set_request_configuration(tag_for_child_directed_treatment=True, max_ad_content_rating="G")
    await Ads.consent.request_info_async()
    await Ads.consent.show_form_if_required_async()
    if Ads.consent.can_request_ads():
        await Ads.initialize_async()

    ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
    await ad.load_async()                  # raises Ads.AdError on no fill, network errors, ...
    result = await ad.show_async()         # returns once the ad is closed; never raises
    print(result.shown, result.error)

    Ads.BannerAd(Ads.TestIds.ADAPTIVE_BANNER)   # a component; anchored adaptive by default

Android and iOS use the Google Mobile Ads SDK. Mac Catalyst and Windows have no ads SDK: `is_available()` is
False, loads raise `AdError("unavailable")` and `show_async()` returns `shown=False`, so an app flow carries on.
The web page and the desktop window show simulated test ads for Google's test unit IDs (`TestIds`) only, so ad
flows can be tried in development; other unit IDs do not fill there.

An app lists `"google-mobile-ads"` in `[tool.pyxpo] modules` and adds the `pyxpo_google_mobile_ads.plugin` config
plugin with its AdMob app IDs (`androidAppId`, `iosAppId`; Google's test app IDs by default).
"""
from __future__ import annotations

import asyncio
import itertools
import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from pyxpo import View, component, use_effect, use_ref, use_state
from pyxpo.module_api import call, constants, native_view, on_event, platform, report, spawn

__version__ = "0.1.0a1"

NATIVE = "GoogleMobileAds"
EVENT = "GoogleMobileAds.event"

__all__ = [
    "AdError", "Reward", "ShowResult", "TestIds", "BannerAdSize", "AdEventType", "MaxAdContentRating",
    "InterstitialAd", "RewardedAd", "RewardedInterstitialAd", "AppOpenAd", "BannerAd", "NativeAd",
    "use_interstitial_ad", "use_rewarded_ad", "use_rewarded_interstitial_ad", "use_app_open_ad",
    "initialize_async", "is_available", "set_request_configuration", "get_request_configuration",
    "request_tracking_async", "get_tracking_status_async", "open_ad_inspector_async", "consent", "ConsentStatus",
    "is_test_id", "AdHandle", "EVENTS",
]


class AdError(Exception):
    """An ad that failed to load or show. `code` is a short name (loads: `no_fill`, `network`, `timeout`,
    `invalid_request`, `app_id_missing`; shows: `not_loaded`, `already_showing`, `already_used`, `not_ready`,
    `app_not_foreground`, `expired`; any: `internal`, `unavailable`, `busy`), `message` the SDK text, `sdk_code` the
    SDK's number when it gave one."""

    def __init__(self, code: str, message: str = "", sdk_code: Optional[int] = None, domain: str = "") -> None:
        super().__init__("%s: %s" % (code, message) if message else code)
        self.code, self.message, self.sdk_code, self.domain = code, message, sdk_code, domain


class Reward:
    """What a rewarded ad gives: `type` ("coins", ...) and `amount`, as set for the unit in AdMob."""

    def __init__(self, type: str, amount: int) -> None:
        self.type, self.amount = type, amount

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Reward) and (self.type, self.amount) == (other.type, other.amount)

    def __repr__(self) -> str:
        return "Reward(%r, %r)" % (self.type, self.amount)


class ShowResult:
    """What `show_async()` returns: `shown` (the ad was on screen), `error` (an `AdError` when it was not, or when it
    failed while showing), `reward` (rewarded types: the `Reward` when it was earned, else None)."""

    def __init__(self, shown: bool, error: Optional[AdError] = None, reward: Optional[Reward] = None) -> None:
        self.shown, self.error, self.reward = shown, error, reward

    def __repr__(self) -> str:
        return "ShowResult(shown=%r, error=%r, reward=%r)" % (self.shown, self.error, self.reward)


class _ByPlatform(type):
    def __getattr__(cls, name: str) -> str:
        table = cls._ios if platform() == "ios" else cls._android
        if name not in table:
            raise AttributeError(name)
        return table[name]


class TestIds(metaclass=_ByPlatform):
    """Google's test ad unit IDs for the running platform (Android ones on the web and desktop). They always fill
    with ads marked "Test Ad" and are safe to click. Use them in development; real IDs from your own account show
    real ads, and clicking those yourself breaks AdMob policy."""
    _android = {
        "APP_OPEN": "ca-app-pub-3940256099942544/9257395921",
        "ADAPTIVE_BANNER": "ca-app-pub-3940256099942544/9214589741",
        "BANNER": "ca-app-pub-3940256099942544/6300978111",
        "INTERSTITIAL": "ca-app-pub-3940256099942544/1033173712",
        "REWARDED": "ca-app-pub-3940256099942544/5224354917",
        "REWARDED_INTERSTITIAL": "ca-app-pub-3940256099942544/5354046379",
        "NATIVE": "ca-app-pub-3940256099942544/2247696110",
        "APP_ID": "ca-app-pub-3940256099942544~3347511713",
    }
    _ios = {
        "APP_OPEN": "ca-app-pub-3940256099942544/5575463023",
        "ADAPTIVE_BANNER": "ca-app-pub-3940256099942544/2435281174",
        "BANNER": "ca-app-pub-3940256099942544/2934735716",
        "INTERSTITIAL": "ca-app-pub-3940256099942544/4411468910",
        "REWARDED": "ca-app-pub-3940256099942544/1712485313",
        "REWARDED_INTERSTITIAL": "ca-app-pub-3940256099942544/6978759866",
        "NATIVE": "ca-app-pub-3940256099942544/3986624511",
        "APP_ID": "ca-app-pub-3940256099942544~1458002511",
    }


_TEST_UNITS = frozenset(v for t in (TestIds._android, TestIds._ios) for k, v in t.items() if k != "APP_ID")
UNIT_RE = re.compile(r"^ca-app-pub-\d{16}/\d{10}$")


def is_test_id(unit_id: str) -> bool:
    """True for Google's test unit IDs (`TestIds`)."""
    return unit_id in _TEST_UNITS


class BannerAdSize:
    """Banner sizes. Adaptive banners pick the best height for the width (the view's width, else the screen's);
    the others are fixed in points/dp. A custom size is `"WIDTHxHEIGHT"`, like `"320x60"`."""
    ANCHORED_ADAPTIVE_BANNER = "ANCHORED_ADAPTIVE_BANNER"
    INLINE_ADAPTIVE_BANNER = "INLINE_ADAPTIVE_BANNER"
    BANNER = "BANNER"
    LARGE_BANNER = "LARGE_BANNER"
    MEDIUM_RECTANGLE = "MEDIUM_RECTANGLE"
    FULL_BANNER = "FULL_BANNER"
    LEADERBOARD = "LEADERBOARD"


FIXED_SIZES = {"BANNER": (320, 50), "LARGE_BANNER": (320, 100), "MEDIUM_RECTANGLE": (300, 250),
               "FULL_BANNER": (468, 60), "LEADERBOARD": (728, 90)}
ADAPTIVE_SIZES = ("ANCHORED_ADAPTIVE_BANNER", "INLINE_ADAPTIVE_BANNER")


class AdEventType:
    """Events of full-screen ads (`add_listener`) and banners (`on_<event>` props)."""
    LOADED = "loaded"
    FAILED = "failed"            # load failed (data: AdError)
    OPENED = "opened"            # full-screen content shown / banner opened an overlay
    IMPRESSION = "impression"
    CLICKED = "clicked"
    CLOSED = "closed"
    SHOW_FAILED = "show_failed"  # data: AdError
    EARNED_REWARD = "earned_reward"  # data: Reward
    PAID = "paid"                # data: {"value_micros", "currency_code", "precision"}


EVENTS = ("loaded", "failed", "opened", "impression", "clicked", "closed", "show_failed", "earned_reward", "paid")


class MaxAdContentRating:
    G = "G"      # all audiences
    PG = "PG"    # parental guidance
    T = "T"      # teens and older
    MA = "MA"    # mature audiences


class ConsentStatus:
    UNKNOWN = "unknown"
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    OBTAINED = "obtained"


def _native() -> bool:
    return platform() in ("ios", "android")


def is_available() -> bool:
    """True where ads can show: Android and iOS with the module linked (not Mac Catalyst), and the web page and
    desktop window (simulated test ads)."""
    if not _native():
        return True
    return bool(constants(NATIVE).get("available"))


def _sim_only(unit_id: str) -> bool:
    """The web page and desktop window only fill Google's test units, with simulated ads."""
    return not _native() and not is_test_id(unit_id)


# -- request configuration --------------------------------------------------------------------------------------

_config: Dict[str, Any] = {"test_device_ids": [], "tag_for_child_directed_treatment": None,
                           "tag_for_under_age_of_consent": None, "max_ad_content_rating": None}


def _tri(name: str, v: Any) -> Optional[bool]:
    if v is not None and not isinstance(v, bool):
        raise ValueError("%s must be True, False or None, got %r" % (name, v))
    return v


def set_request_configuration(*, test_device_ids: Optional[Sequence[str]] = None,
                              tag_for_child_directed_treatment: Optional[bool] = None,
                              tag_for_under_age_of_consent: Optional[bool] = None,
                              max_ad_content_rating: Optional[str] = None) -> None:
    """Settings for every ad request, kept until changed (arguments left at None keep their value).

    `tag_for_child_directed_treatment=True` tells Google the app is for children (COPPA: no personalized ads,
    no remarketing); `tag_for_under_age_of_consent=True` treats the user as under the age of consent (GDPR).
    Either one also makes requests non-personalized. `max_ad_content_rating` is "G", "PG", "T" or "MA".
    `test_device_ids` are hashed device IDs (printed in the device log) that get test ads for real unit IDs.
    Applied by `initialize_async()`, and right away when the SDK already runs."""
    if test_device_ids is not None:
        ids = list(test_device_ids)
        if not all(isinstance(i, str) and i for i in ids):
            raise ValueError("test_device_ids must be a list of strings")
        _config["test_device_ids"] = ids
    for name, v in (("tag_for_child_directed_treatment", tag_for_child_directed_treatment),
                    ("tag_for_under_age_of_consent", tag_for_under_age_of_consent)):
        if v is not None:
            _config[name] = _tri(name, v)
    if max_ad_content_rating is not None:
        if max_ad_content_rating not in ("G", "PG", "T", "MA"):
            raise ValueError("max_ad_content_rating must be G, PG, T or MA, got %r" % (max_ad_content_rating,))
        _config["max_ad_content_rating"] = max_ad_content_rating
    if _state["initialized"] and _native():
        call(NATIVE, "setRequestConfiguration", _config_wire())


def get_request_configuration() -> Dict[str, Any]:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in _config.items()}


def _config_wire() -> Dict[str, Any]:
    return {"testDeviceIds": list(_config["test_device_ids"]),
            "tagForChildDirectedTreatment": _config["tag_for_child_directed_treatment"],
            "tagForUnderAgeOfConsent": _config["tag_for_under_age_of_consent"],
            "maxAdContentRating": _config["max_ad_content_rating"]}


def _request_wire(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-request options: `keywords` (list of strings), `content_url` (the page the ad is next to),
    `neighboring_content_urls`, `non_personalized` (True asks for non-personalized ads; forced on for
    child-directed and under-age requests)."""
    o = dict(options or {})
    unknown = set(o) - {"keywords", "content_url", "neighboring_content_urls", "non_personalized"}
    if unknown:
        raise ValueError("unknown request option(s): %s" % ", ".join(sorted(unknown)))
    kw = o.get("keywords") or []
    if not isinstance(kw, (list, tuple)) or not all(isinstance(k, str) and k for k in kw):
        raise ValueError("keywords must be a list of strings")
    urls = o.get("neighboring_content_urls") or []
    for u in [o.get("content_url")] + list(urls):
        if u is not None and (not isinstance(u, str) or not re.match(r"^https?://", u) or len(u) > 512):
            raise ValueError("content URLs must be http(s) URLs up to 512 characters, got %r" % (u,))
    npa = _tri("non_personalized", o.get("non_personalized"))
    if _config["tag_for_child_directed_treatment"] or _config["tag_for_under_age_of_consent"]:
        npa = True
    return {"keywords": list(kw), "contentUrl": o.get("content_url"), "neighboringContentUrls": list(urls),
            "nonPersonalized": bool(npa)}


def _unit(unit_id: Any) -> str:
    if not isinstance(unit_id, str) or not UNIT_RE.match(unit_id):
        raise ValueError("unit_id must be an AdMob ad unit ID like ca-app-pub-0000000000000000/0000000000, got %r"
                         % (unit_id,))
    return unit_id


# -- setup --------------------------------------------------------------------------------------------------------

_state: Dict[str, Any] = {"initialized": False, "init": None}
_on_ready: List[Callable[[], None]] = []      # banner/native views waiting for initialize_async
_ready_waiters: List["asyncio.Future[None]"] = []   # full-screen loads waiting for it


async def _until_initialized() -> None:
    """Ads load only once `initialize_async()` has finished: the SDK then has the request configuration (a
    child-directed tag must be on the first request) and the consent step has run."""
    if _state["initialized"]:
        return
    fut: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
    _ready_waiters.append(fut)
    await fut


def _set_initialized(res: Dict[str, Any]) -> None:
    _state["initialized"], _state["init"] = True, dict(res or {})
    waiters, _ready_waiters[:] = list(_ready_waiters), []
    for fut in waiters:
        if not fut.done():
            fut.set_result(None)
    views, _on_ready[:] = list(_on_ready), []
    for fn in views:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - one unmounted view must not stop the others
            report(e)


def _use_initialized() -> bool:
    """True once `initialize_async()` has finished; re-renders the component then."""
    ready, set_ready = use_state(bool(_state["initialized"]))

    def watch() -> Optional[Callable[[], None]]:
        if _state["initialized"]:
            set_ready(True)
            return None

        def fn() -> None:
            set_ready(True)

        _on_ready.append(fn)

        def off() -> None:
            if fn in _on_ready:
                _on_ready.remove(fn)

        return off

    use_effect(watch, [])
    return ready or bool(_state["initialized"])


async def initialize_async() -> Dict[str, Any]:
    """Starts the Google Mobile Ads SDK with the request configuration. Call once, after consent (`consent`).
    Ads (full-screen loads, banners and native ads) wait for it, so the first request already carries the
    configuration. Returns {adapter name: {"state": "ready" | "not_ready", "description", "latency"}}; {} where
    ads are not available. Calling it again returns the first result."""
    if _state["initialized"]:
        return dict(_state["init"] or {})
    if not is_available():
        return {}
    if _native():
        res = await call(NATIVE, "initialize", _config_wire(), reply=True)
    else:
        res = {"simulated": {"state": "ready", "description": "pyxpo simulated test ads", "latency": 0}}
    _set_initialized(res)
    return dict(_state["init"])


async def request_tracking_async() -> str:
    """Asks for App Tracking Transparency on iOS 14+ (the prompt shows once). Returns "authorized", "denied",
    "restricted" or "not_determined"; "unavailable" on other platforms. Do not ask in apps for children (Apple's
    Kids Category does not allow tracking)."""
    if platform() != "ios" or not is_available():
        return "unavailable"
    return str(await call(NATIVE, "requestTracking", reply=True))


async def get_tracking_status_async() -> str:
    if platform() != "ios" or not is_available():
        return "unavailable"
    return str(await call(NATIVE, "trackingStatus", reply=True))


async def open_ad_inspector_async() -> None:
    """Opens Google's Ad Inspector (test devices only) to check mediation and ad unit setup."""
    if not _native() or not is_available():
        raise AdError("unavailable", "the Ad Inspector needs Android or iOS")
    res = await call(NATIVE, "openAdInspector", reply=True)
    if isinstance(res, dict) and res.get("error"):
        raise _error(res)


# -- consent (Google User Messaging Platform) -------------------------------------------------------------------

class _Consent:
    """Consent for the EEA, the UK and Switzerland (and US state rules) through Google's User Messaging Platform.
    Set up the messages in AdMob > Privacy & messaging. Call `request_info_async()` on every app start, then
    `show_form_if_required_async()`, then load ads when `can_request_ads()`."""

    def __init__(self) -> None:
        self._info = {"status": ConsentStatus.UNKNOWN, "can_request_ads": False, "privacy_options_required": False,
                      "form_available": False}

    def _set(self, res: Any) -> Dict[str, Any]:
        if isinstance(res, dict) and res.get("error"):
            raise _error(res)
        res = res if isinstance(res, dict) else {}
        status = res.get("status", ConsentStatus.UNKNOWN)
        self._info = {"status": status if status in ("unknown", "required", "not_required", "obtained") else "unknown",
                      "can_request_ads": bool(res.get("canRequestAds")),
                      "privacy_options_required": bool(res.get("privacyOptionsRequired")),
                      "form_available": bool(res.get("formAvailable"))}
        return dict(self._info)

    def _no_native(self) -> Dict[str, Any]:
        return self._set({"status": "not_required", "canRequestAds": is_available()})

    async def request_info_async(self, *, debug_geography: Optional[str] = None,
                                 test_device_ids: Optional[Sequence[str]] = None,
                                 tag_for_under_age_of_consent: Optional[bool] = None) -> Dict[str, Any]:
        """Updates the consent state from Google. `debug_geography` ("eea", "regulated_us_state", "other";
        honoured on test devices only) tries the form outside the EEA. Returns `info()`."""
        if debug_geography not in (None, "eea", "regulated_us_state", "other", "disabled"):
            raise ValueError("debug_geography must be eea, regulated_us_state, other or disabled")
        if not _native() or not is_available():
            return self._no_native()
        under = _config["tag_for_under_age_of_consent"] if tag_for_under_age_of_consent is None else \
            _tri("tag_for_under_age_of_consent", tag_for_under_age_of_consent)
        return self._set(await call(NATIVE, "consentRequestInfo", {
            "debugGeography": debug_geography, "testDeviceIds": list(test_device_ids or _config["test_device_ids"]),
            "tagForUnderAgeOfConsent": bool(under)}, reply=True))

    async def show_form_if_required_async(self) -> Dict[str, Any]:
        """Shows the consent form when the user must answer it; returns `info()` after it is dismissed."""
        if not _native() or not is_available():
            return self._no_native()
        return self._set(await call(NATIVE, "consentShowFormIfRequired", reply=True))

    async def show_privacy_options_form_async(self) -> Dict[str, Any]:
        """Shows the privacy options form so the user can change their choice (offer it in settings when
        `info()["privacy_options_required"]`)."""
        if not _native() or not is_available():
            return self._no_native()
        return self._set(await call(NATIVE, "consentShowPrivacyOptionsForm", reply=True))

    def info(self) -> Dict[str, Any]:
        """{"status", "can_request_ads", "privacy_options_required", "form_available"} from the last update."""
        return dict(self._info)

    def can_request_ads(self) -> bool:
        return self._info["can_request_ads"]

    async def reset_async(self) -> None:
        """Forgets the stored consent (testing only)."""
        if _native() and is_available():
            await call(NATIVE, "consentReset", reply=True)
        self._info = {"status": ConsentStatus.UNKNOWN, "can_request_ads": False, "privacy_options_required": False,
                      "form_available": False}


consent = _Consent()


# -- full-screen ads ----------------------------------------------------------------------------------------------

_ids = itertools.count(1)
_live: Dict[int, "_FullScreenAd"] = {}
_subscribed = [False]


def _error(d: Dict[str, Any]) -> AdError:
    code = d.get("code")
    return AdError(str(d.get("error") or "internal"), str(d.get("message") or ""),
                   code if isinstance(code, int) and not isinstance(code, bool) else None, str(d.get("domain") or ""))


def _route(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    ad = _live.get(payload.get("id"))  # type: ignore[arg-type]
    if ad is not None:
        ad._native_event(str(payload.get("event", "")), payload.get("data"))


def _subscribe() -> None:
    if not _subscribed[0]:
        on_event(EVENT, _route)
        _subscribed[0] = True


class _Sub:
    def __init__(self, off: Callable[[], None]) -> None:
        self._off = off

    def remove(self) -> None:
        self._off()


class _FullScreenAd:
    """Shared shape of the full-screen ad types. One object can be loaded and shown again and again (each load
    makes a new SDK ad); `load_async()` while a load runs waits for that load."""
    TYPE = ""
    TTL = 3600.0          # Google: a loaded ad is good for an hour (app open: 4 hours)
    REWARDED = False
    clock: Callable[[], float] = staticmethod(time.monotonic)

    def __init__(self, unit_id: str, request_options: Optional[Dict[str, Any]] = None, *,
                 immersive: bool = False) -> None:
        self.unit_id = _unit(unit_id)
        self._request = _request_wire(request_options)  # checked now, so a bad option fails at the call site
        self._options = dict(request_options or {})
        self.immersive = bool(immersive)
        self._listeners: Dict[str, List[Callable[[Any], Any]]] = {}
        self._id = 0                       # native id of the current SDK ad (0: none)
        self._loaded_at: Optional[float] = None
        self._loading: Optional["asyncio.Future[None]"] = None
        self._showing: Optional["asyncio.Future[ShowResult]"] = None
        self._reward: Optional[Reward] = None
        self._show_error: Optional[AdError] = None
        self._opened = False
        self.error: Optional[AdError] = None

    # state
    @property
    def loaded(self) -> bool:
        """A loaded ad that has not expired and is not on screen."""
        return self._loaded_at is not None and self._showing is None and not self._expired()

    @property
    def loading(self) -> bool:
        return self._loading is not None

    @property
    def showing(self) -> bool:
        return self._showing is not None

    def _expired(self) -> bool:
        return self._loaded_at is not None and self.clock() - self._loaded_at > self.TTL

    def add_listener(self, event: str, fn: Callable[[Any], Any]) -> _Sub:
        """Calls `fn(data)` on `event` (`AdEventType`). Returns a subscription with `remove()`."""
        if event not in EVENTS:
            raise ValueError("unknown ad event %r (one of %s)" % (event, ", ".join(EVENTS)))
        if event == "earned_reward" and not self.REWARDED:
            raise ValueError("%s has no earned_reward event" % type(self).__name__)
        self._listeners.setdefault(event, []).append(fn)

        def off() -> None:
            lst = self._listeners.get(event, [])
            if fn in lst:
                lst.remove(fn)

        return _Sub(off)

    def remove_all_listeners(self) -> None:
        self._listeners.clear()

    def _fire(self, event: str, data: Any = None) -> None:
        for fn in list(self._listeners.get(event, ())):
            try:
                fn(data)
            except Exception as e:  # noqa: BLE001 - one bad listener must not break the ad
                report(e)

    def _load_args(self) -> Dict[str, Any]:
        return {"id": self._id, "type": self.TYPE, "unitId": self.unit_id, "request": _request_wire(self._options)}

    async def load_async(self) -> None:
        """Loads an ad. Returns at once when one is loaded; raises `AdError` when the load fails (also fires
        "failed"). Load ahead of time: a load takes from a few hundred ms to several seconds. A load started
        before `initialize_async()` has finished waits for it."""
        if self._showing is not None:
            raise AdError("busy", "the ad is on screen")
        if self.loaded:
            return
        if self._loading is not None:
            return await asyncio.shield(self._loading)
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[None]" = loop.create_future()
        self._loading = fut
        self._drop()
        self._id = next(_ids)
        self.error = None
        try:
            if not is_available() or _sim_only(self.unit_id):
                raise AdError("unavailable", "no ads on this platform" if _native() or not is_available()
                              else "the web page and desktop window only fill Google's test unit IDs")
            await _until_initialized()
            _subscribe()
            _live[self._id] = self
            try:
                res = await call(NATIVE, "load", self._load_args(), reply=True)
            except AdError:
                raise
            except Exception as e:  # noqa: BLE001 - no module behind the call (desktop without the page side)
                raise AdError("unavailable", str(e))
            if isinstance(res, dict) and res.get("error"):
                raise _error(res)
            self._loaded_at = self.clock()
        except AdError as e:
            _live.pop(self._id, None)
            self._id, self.error = 0, e
            self._loading = None
            if not fut.done():
                fut.set_exception(e)
                fut.exception()  # retrieved: a load nobody else awaits is not "never retrieved"
            self._fire("failed", e)
            raise
        self._loading = None
        fut.set_result(None)
        self._fire("loaded", None)

    async def show_async(self) -> ShowResult:
        """Shows the loaded ad and returns when it is closed. Never raises: `ShowResult(shown=False, error=...)`
        when there is no loaded ad, it expired, another one is on screen, or showing failed."""
        if self._showing is not None:
            return ShowResult(False, AdError("already_showing", "this ad is already on screen"))
        if self._loaded_at is None:
            return ShowResult(False, AdError("not_loaded", "call load_async() first"))
        if self._expired():
            self._drop()
            return ShowResult(False, AdError("expired", "the ad was loaded over %d minutes ago" % (self.TTL // 60)))
        if _showing_any[0] is not None and _showing_any[0] is not self:
            return ShowResult(False, AdError("already_showing", "another full-screen ad is on screen"))
        loop = asyncio.get_running_loop()
        self._showing = loop.create_future()
        _showing_any[0] = self
        self._reward, self._show_error, self._opened = None, None, False
        try:
            res = await call(NATIVE, "show", {"id": self._id, "immersive": self.immersive}, reply=True)
            if isinstance(res, dict) and res.get("error"):
                self._show_error = _error(res)
            elif isinstance(res, dict) and res.get("reward") and self._reward is None:
                r = res["reward"]
                self._reward = Reward(str(r.get("type", "")), int(r.get("amount", 0)))
            shown = bool(res.get("shown")) if isinstance(res, dict) else False
            result = ShowResult(shown or self._opened, self._show_error, self._reward)
        except Exception as e:  # noqa: BLE001
            result = ShowResult(self._opened, AdError("internal", str(e)), self._reward)
        finally:
            fut, self._showing = self._showing, None
            if _showing_any[0] is self:
                _showing_any[0] = None
            # an SDK ad shows once, and native releases it when the show ends
            _live.pop(self._id, None)
            self._id, self._loaded_at = 0, None
        if fut is not None and not fut.done():
            fut.set_result(result)
        return result

    def _native_event(self, event: str, data: Any) -> None:
        if event == "opened":
            self._opened = True
        elif event == "earned_reward":
            d = data if isinstance(data, dict) else {}
            self._reward = Reward(str(d.get("type", "")), int(d.get("amount", 0)))
            data = self._reward
        elif event == "show_failed":
            self._show_error = _error(data if isinstance(data, dict) else {})
            data = self._show_error
        elif event == "paid":
            d = data if isinstance(data, dict) else {}
            data = {"value_micros": int(d.get("valueMicros", 0)), "currency_code": str(d.get("currencyCode", "")),
                    "precision": str(d.get("precision", "unknown"))}
        if event in EVENTS and event not in ("loaded", "failed"):
            self._fire(event, data)

    def _drop(self) -> None:
        """Releases the current SDK ad (native keeps it until shown or dropped)."""
        if self._id:
            _live.pop(self._id, None)
            if self._loaded_at is not None and _native() and is_available():
                call(NATIVE, "destroy", {"id": self._id})
        self._id, self._loaded_at = 0, None

    def destroy(self) -> None:
        """Releases the ad and its listeners (an ad on screen stays until closed)."""
        if self._showing is None:
            self._drop()
        self.remove_all_listeners()

    def __repr__(self) -> str:
        state = "showing" if self.showing else "loaded" if self.loaded else "loading" if self.loading else "idle"
        return "%s(%r, %s)" % (type(self).__name__, self.unit_id, state)


_showing_any: List[Optional[_FullScreenAd]] = [None]


class InterstitialAd(_FullScreenAd):
    """A full-screen ad the user closes (skippable after a few seconds for video). Show it at a natural break,
    like between levels, never right after a tap that expects something else."""
    TYPE = "interstitial"


class RewardedAd(_FullScreenAd):
    """A full-screen ad the user chooses to watch for a reward (`ShowResult.reward`, "earned_reward" event).
    `user_id` and `custom_data` go to server-side verification callbacks."""
    TYPE = "rewarded"
    REWARDED = True

    def __init__(self, unit_id: str, request_options: Optional[Dict[str, Any]] = None, *, immersive: bool = False,
                 user_id: Optional[str] = None, custom_data: Optional[str] = None) -> None:
        super().__init__(unit_id, request_options, immersive=immersive)
        for name, v in (("user_id", user_id), ("custom_data", custom_data)):
            if v is not None and (not isinstance(v, str) or len(v) > 1024):
                raise ValueError("%s must be a string up to 1024 characters" % name)
        self.user_id, self.custom_data = user_id, custom_data

    def _load_args(self) -> Dict[str, Any]:
        a = super()._load_args()
        if self.user_id is not None or self.custom_data is not None:
            a["serverSideVerification"] = {"userId": self.user_id, "customData": self.custom_data}
        return a


class RewardedInterstitialAd(RewardedAd):
    """A rewarded ad shown at a natural break without an opt-in button; tell the user about the reward first
    and let them skip it."""
    TYPE = "rewarded_interstitial"


class AppOpenAd(_FullScreenAd):
    """A full-screen ad for when the app comes to the front (from the background or a cold start). Loaded ads
    keep for 4 hours. Show it at most once per foreground, and not on the very first launch."""
    TYPE = "app_open"
    TTL = 4 * 3600.0


# -- hooks ------------------------------------------------------------------------------------------------------

class AdHandle:
    """What the `use_*_ad` hooks return: `loaded`, `loading`, `error`, `reward` (of the last show), `ad` (the
    underlying ad object), `load()` and `show_async()`."""

    def __init__(self, ad: _FullScreenAd, snap: Dict[str, Any], load: Callable[[], None],
                 show: Callable[[], Any]) -> None:
        self.ad = ad
        self.loaded, self.loading = snap["loaded"], snap["loading"]
        self.error, self.reward = snap["error"], snap["reward"]
        self.load = load
        self.show_async = show

    def __repr__(self) -> str:
        return "AdHandle(loaded=%r, loading=%r, error=%r)" % (self.loaded, self.loading, self.error)


def _use_ad(cls: Any, unit_id: str, request_options: Optional[Dict[str, Any]], preload: bool,
            reload: bool, kwargs: Dict[str, Any]) -> AdHandle:
    ref = use_ref(None)
    if ref.current is None or ref.current.unit_id != unit_id:
        if ref.current is not None:
            ref.current.destroy()
        ref.current = cls(unit_id, request_options, **kwargs)
    ad = ref.current
    snap, set_snap = use_state({"loaded": False, "loading": False, "error": None, "reward": None})
    alive = use_ref(True)

    def sync(**extra: Any) -> None:
        if alive.current:
            set_snap(lambda s: dict(s, loaded=ad.loaded, loading=ad.loading, error=ad.error, **extra))

    async def _load() -> None:
        try:
            await ad.load_async()
        except AdError:
            pass
        sync()

    def load() -> None:
        if not ad.loaded and not ad.loading and not ad.showing:
            spawn(_load())
            sync()

    async def show() -> ShowResult:
        res = await ad.show_async()
        sync(reward=res.reward)
        if reload:
            load()
        return res

    def mount() -> Callable[[], None]:
        alive.current = True
        if preload:
            load()

        def unmount() -> None:
            alive.current = False
            ad.destroy()

        return unmount

    use_effect(mount, [ad])
    return AdHandle(ad, snap, load, show)


def use_interstitial_ad(unit_id: str, request_options: Optional[Dict[str, Any]] = None, *, preload: bool = True,
                        reload: bool = True, immersive: bool = False) -> AdHandle:
    """An interstitial for this component: loads on mount (`preload`), loads the next one after each show
    (`reload`), and is released on unmount. `handle.loaded` re-renders the component when it changes."""
    return _use_ad(InterstitialAd, unit_id, request_options, preload, reload, {"immersive": immersive})


def use_rewarded_ad(unit_id: str, request_options: Optional[Dict[str, Any]] = None, *, preload: bool = True,
                    reload: bool = True, immersive: bool = False, user_id: Optional[str] = None,
                    custom_data: Optional[str] = None) -> AdHandle:
    """Like `use_interstitial_ad` for a rewarded ad; `handle.reward` is the reward of the last show."""
    return _use_ad(RewardedAd, unit_id, request_options, preload, reload,
                   {"immersive": immersive, "user_id": user_id, "custom_data": custom_data})


def use_rewarded_interstitial_ad(unit_id: str, request_options: Optional[Dict[str, Any]] = None, *,
                                 preload: bool = True, reload: bool = True, immersive: bool = False,
                                 user_id: Optional[str] = None, custom_data: Optional[str] = None) -> AdHandle:
    return _use_ad(RewardedInterstitialAd, unit_id, request_options, preload, reload,
                   {"immersive": immersive, "user_id": user_id, "custom_data": custom_data})


def use_app_open_ad(unit_id: str, request_options: Optional[Dict[str, Any]] = None, *, preload: bool = True,
                    reload: bool = True) -> AdHandle:
    """An app open ad; show it from an `AppState` "active" listener."""
    return _use_ad(AppOpenAd, unit_id, request_options, preload, reload, {})


# -- banner and native views ------------------------------------------------------------------------------------

_Banner = native_view("PxBannerAd")
_Native = native_view("PxNativeAd")

_VIEW_EVENTS = ("load", "error", "open", "close", "click", "impression", "paid")


def _banner_size(size: str) -> Optional[tuple]:
    """(width, height) of a fixed size; None for adaptive sizes."""
    if size in ADAPTIVE_SIZES:
        return None
    if size in FIXED_SIZES:
        return FIXED_SIZES[size]
    m = re.match(r"^(\d{2,4})x(\d{2,4})$", str(size))
    if not m or not (32 <= int(m.group(1)) <= 2048 and 32 <= int(m.group(2)) <= 2048):
        raise ValueError("size must be a BannerAdSize name or 'WIDTHxHEIGHT' (32-2048), got %r" % (size,))
    return int(m.group(1)), int(m.group(2))


def _view_events(props: Dict[str, Any], handlers: Dict[str, Optional[Callable[[Any], Any]]],
                 sized: Callable[[Any], None]) -> None:
    def wrap(name: str, fn: Optional[Callable[[Any], Any]]) -> Optional[Callable[[Any], Any]]:
        if name == "error":
            return (lambda d: fn(_error(d if isinstance(d, dict) else {}))) if fn else None
        if name == "paid":
            return (lambda d: fn({"value_micros": int((d or {}).get("valueMicros", 0)),
                                  "currency_code": str((d or {}).get("currencyCode", "")),
                                  "precision": str((d or {}).get("precision", "unknown"))})) if fn else None
        return fn

    for name in _VIEW_EVENTS:
        fn = wrap(name, handlers.get(name))
        if fn is not None:
            props["on_" + name] = fn
    props["on_size"] = sized


def BannerAd(unit_id: str, size: str = BannerAdSize.ANCHORED_ADAPTIVE_BANNER, **props: Any) -> Any:
    """A banner ad. Adaptive sizes take the width of the space they are in and size their height to the ad
    (0 until one loads, so nothing shows when there is no fill); fixed sizes reserve their box. Keyword props:
    `request_options`, `max_height` (caps an inline adaptive banner), `on_load({"width", "height"})`,
    `on_error(AdError)`, `on_open`, `on_close`, `on_click`, `on_impression`, `on_paid({...})`, `style`, `key`.
    Nothing loads until `initialize_async()` has finished. Where ads are not available it renders nothing."""
    _unit(unit_id)
    _banner_size(size)
    mh = props.get("max_height")
    if mh is not None and (isinstance(mh, bool) or not isinstance(mh, int) or not 32 <= mh <= 2048):
        raise ValueError("max_height must be an int from 32 to 2048")
    _request_wire(props.get("request_options"))
    return _BannerAd(unit_id=unit_id, size=size, **props)


@component
def _BannerAd(*, unit_id: str, size: str,
             request_options: Optional[Dict[str, Any]] = None, max_height: Optional[int] = None,
             on_load: Optional[Callable[[Any], Any]] = None, on_error: Optional[Callable[[AdError], Any]] = None,
             on_open: Optional[Callable[[Any], Any]] = None, on_close: Optional[Callable[[Any], Any]] = None,
             on_click: Optional[Callable[[Any], Any]] = None, on_impression: Optional[Callable[[Any], Any]] = None,
             on_paid: Optional[Callable[[Any], Any]] = None, style: Any = None) -> Any:
    fixed = _banner_size(size)
    height, set_height = use_state(fixed[1] if fixed else 0)
    ready = _use_initialized()
    if not is_available() or _sim_only(unit_id):
        return None

    def sized(d: Any) -> None:
        h = (d or {}).get("height") if isinstance(d, dict) else None
        if isinstance(h, (int, float)) and math.isfinite(h) and 0 <= h <= 4096:
            set_height(int(round(h)))

    box: Dict[str, Any] = {"height": height, "align_self": "center"} if fixed else {"height": height, "align_self": "stretch"}
    if fixed:
        box["width"] = fixed[0]
    if style:
        box.update(style if isinstance(style, dict) else {})
    if not ready:
        # No request before initialize_async: the request it would send changes once the configuration is in
        # (a second load). A fixed size keeps its box meanwhile.
        return View(style=box) if fixed else None
    props: Dict[str, Any] = {"unit_id": unit_id, "size": size, "request": _request_wire(request_options),
                             "max_height": max_height}
    _view_events(props, {"load": on_load, "error": on_error, "open": on_open, "close": on_close,
                         "click": on_click, "impression": on_impression, "paid": on_paid}, sized)
    return _Banner(style=box, **props)


NATIVE_TEMPLATES = {"small": 96, "medium": 330}


def NativeAd(unit_id: str, template: str = "medium", **props: Any) -> Any:
    """A native ad drawn natively from Google's template layout: "small" (icon, headline, call to action, about
    96 high) or "medium" (adds the media view and body text, about 330 high). It always shows the "Ad" badge and
    the AdChoices icon, as AdMob policy needs. Collapsed to 0 height until an ad loads (and nothing loads
    until `initialize_async()` has finished). Keyword props:
    `request_options`, `background_color`, `text_color`, `button_color`, `button_text_color`, `on_load`,
    `on_error(AdError)`, `on_click`, `on_impression`, `on_paid`, `style`, `key`."""
    _unit(unit_id)
    if template not in NATIVE_TEMPLATES:
        raise ValueError("template must be 'small' or 'medium', got %r" % (template,))
    _request_wire(props.get("request_options"))
    return _NativeAd(unit_id=unit_id, template=template, **props)


@component
def _NativeAd(*, unit_id: str, template: str, request_options: Optional[Dict[str, Any]] = None,
             background_color: Any = None, text_color: Any = None, button_color: Any = None,
             button_text_color: Any = None, on_load: Optional[Callable[[Any], Any]] = None,
             on_error: Optional[Callable[[AdError], Any]] = None, on_click: Optional[Callable[[Any], Any]] = None,
             on_impression: Optional[Callable[[Any], Any]] = None, on_paid: Optional[Callable[[Any], Any]] = None,
             style: Any = None) -> Any:
    from pyxpo.style import parse_color
    height, set_height = use_state(0)
    ready = _use_initialized()
    if not is_available() or _sim_only(unit_id) or not ready:
        return None

    def sized(d: Any) -> None:
        h = (d or {}).get("height") if isinstance(d, dict) else None
        if isinstance(h, (int, float)) and math.isfinite(h) and 0 <= h <= 4096:
            set_height(int(round(h)))

    props: Dict[str, Any] = {"unit_id": unit_id, "template": template, "request": _request_wire(request_options)}
    for name, c in (("background_color", background_color), ("text_color", text_color),
                    ("button_color", button_color), ("button_text_color", button_text_color)):
        props[name] = None if c is None else parse_color(c)
    _view_events(props, {"load": on_load, "error": on_error, "click": on_click, "impression": on_impression,
                         "paid": on_paid}, sized)
    box: Dict[str, Any] = {"height": height, "align_self": "stretch"}
    if style:
        box.update(style if isinstance(style, dict) else {})
    return _Native(style=box, **props)


# camelCase aliases (the react-native-google-mobile-ads names)
useInterstitialAd = use_interstitial_ad
useRewardedAd = use_rewarded_ad
useRewardedInterstitialAd = use_rewarded_interstitial_ad
useAppOpenAd = use_app_open_ad
