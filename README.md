# pyxpo-google-mobile-ads

Google AdMob ads for [pyxpo](https://github.com/pyxpo/pyxpo) apps: banner, interstitial, rewarded, rewarded
interstitial, app open and native ads, with consent (Google's User Messaging Platform) and request settings. The API
follows [react-native-google-mobile-ads](https://docs.page/invertase/react-native-google-mobile-ads) in pyxpo's
Python style. Android uses `play-services-ads`, iOS the GoogleMobileAds Swift package.

```sh
pip install pyxpo-google-mobile-ads
```

```toml
[tool.pyxpo]
modules = ["google-mobile-ads"]
# Your AdMob app IDs (AdMob > Apps > App settings). Leave them out to use Google's test app IDs.
plugins = [["pyxpo_google_mobile_ads.plugin", { androidAppId = "ca-app-pub-XXXXXXXXXXXXXXXX~YYYYYYYYYY", iosAppId = "ca-app-pub-XXXXXXXXXXXXXXXX~ZZZZZZZZZZ" }]]
```

```python
import pyxpo_google_mobile_ads as Ads

async def start():
    await Ads.consent.request_info_async()          # every app start (EEA, UK, US states)
    await Ads.consent.show_form_if_required_async()
    if Ads.consent.can_request_ads():
        await Ads.initialize_async()

# Full-screen ads: load ahead, show at a natural break.
ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
await ad.load_async()                 # raises Ads.AdError on no fill or no network
result = await ad.show_async()        # returns after the user closes it; never raises
if not result.shown:
    print(result.error.code)          # not_loaded, expired, already_showing, ...

# In a component: the hook preloads, and loads the next ad after each show.
@component
def Level():
    ad = Ads.use_rewarded_ad(Ads.TestIds.REWARDED)
    async def watch():
        r = await ad.show_async()
        if r.reward:
            give_coins(r.reward.amount)
    return Button("Watch for coins", on_press=watch, disabled=not ad.loaded)  # async handlers run as tasks

# Views
Ads.BannerAd(Ads.TestIds.ADAPTIVE_BANNER)                       # anchored adaptive: full width, ad height
Ads.BannerAd(Ads.TestIds.BANNER, size=Ads.BannerAdSize.MEDIUM_RECTANGLE)
Ads.NativeAd(Ads.TestIds.NATIVE, template="small", background_color="#fff8e1")
```

Use `Ads.TestIds` while you build: they always fill with ads marked "Test Ad". Real unit IDs show real ads, and
clicking your own real ads breaks AdMob policy (add your phone with `test_device_ids` instead).

## Functions

| Function | Notes |
| --- | --- |
| `initialize_async()` | Starts the SDK with the request configuration. Call once, after consent. Nothing loads before it has finished: full-screen loads wait for it, banners and native ads render (and request) only then, so the first request already carries the child-directed tag and no ad loads twice. Returns `{adapter: {"state", "description", "latency"}}`; `{}` where ads are not available. |
| `is_available()` | False on Mac Catalyst, Windows and builds without the module: there `show_async()` returns "not shown" and the views render nothing, so the app flow carries on. |
| `set_request_configuration(...)` / `get_request_configuration()` | `tag_for_child_directed_treatment`, `tag_for_under_age_of_consent`, `max_ad_content_rating` (`"G"`, `"PG"`, `"T"`, `"MA"`), `test_device_ids`. Kept until changed; applied live once the SDK runs. |
| `InterstitialAd`, `RewardedAd`, `RewardedInterstitialAd`, `AppOpenAd` `(unit_id, request_options=None, *, immersive=False)` | `load_async()`, `show_async()` (`ShowResult(shown, error, reward)`), `loaded`, `loading`, `showing`, `error`, `add_listener(event, fn)` (returns `.remove()`), `destroy()`. A loaded ad keeps for 1 hour (app open: 4 hours). Rewarded types also take `user_id` and `custom_data` for server-side verification. |
| `use_interstitial_ad`, `use_rewarded_ad`, `use_rewarded_interstitial_ad`, `use_app_open_ad` `(unit_id, request_options=None, *, preload=True, reload=True)` | Hooks: `loaded`, `loading`, `error`, `reward`, `ad`, `load()`, `show_async()`. Released on unmount. |
| `BannerAd(unit_id, size=ANCHORED_ADAPTIVE_BANNER, ...)` | Sizes in `BannerAdSize` (`BANNER` 320x50, `LARGE_BANNER`, `MEDIUM_RECTANGLE`, `FULL_BANNER`, `LEADERBOARD`, the two adaptive ones) or `"WxH"`. Adaptive banners stay 0 high until an ad loads. Props `request_options`, `max_height`, `on_load`, `on_error`, `on_open`, `on_close`, `on_click`, `on_impression`, `on_paid`, `style`. |
| `NativeAd(unit_id, template="medium", ...)` | Google's template layout, `"small"` or `"medium"`, with the "Ad" badge and AdChoices. Colors `background_color`, `text_color`, `button_color`, `button_text_color`. |
| `consent.request_info_async(debug_geography=None, test_device_ids=None)` | Updates the consent state. `debug_geography` `"eea"` tries the form outside the EEA on test devices. Then `show_form_if_required_async()`, `can_request_ads()`, `info()`, `show_privacy_options_form_async()` (offer it in settings when `info()["privacy_options_required"]`), `reset_async()`. |
| `request_tracking_async()` / `get_tracking_status_async()` | iOS App Tracking Transparency; `"unavailable"` elsewhere. Needs the plugin's `userTrackingUsageDescription`. |
| `open_ad_inspector_async()` | Google's Ad Inspector on test devices. |

Ad events (`AdEventType`): `loaded`, `failed`, `opened`, `impression`, `clicked`, `closed`, `show_failed`,
`earned_reward` (rewarded types), `paid` (`{"value_micros", "currency_code", "precision"}`). Errors are
`AdError(code, message, sdk_code, domain)`; codes include `no_fill`, `network`, `invalid_request`, `app_id_missing`,
`timeout`, `unavailable`, `internal`.

Per-request options: `keywords`, `content_url`, `neighboring_content_urls`, `non_personalized`.

## Config plugin

`pyxpo_google_mobile_ads.plugin` options:

- `androidAppId`, `iosAppId`: AdMob app IDs (with `~`). Default: Google's test app IDs.
- `userTrackingUsageDescription`: the iOS tracking prompt text, needed for `request_tracking_async()`.
- `skAdNetworkItems`: SKAdNetwork IDs (default Google's `cstr6suwn9.skadnetwork`). Before an App Store release add
  Google's full list (developers.google.com/admob/ios/3p-skadnetworks) and your mediation partners'; the SDK logs
  the missing ones on launch.
- `delayAppMeasurementInit`: keep Google's app measurement off until `initialize_async()`.
- `removeAdIdPermission`: drop the Android `AD_ID` permission (Google Play Families policy).

App IDs are not secret, but keep real ones in pyproject.toml, not in code.

## Apps for children

Google Play's Families policy and Apple's Kids Category have extra rules. For an app whose audience includes
children:

```python
Ads.set_request_configuration(tag_for_child_directed_treatment=True, max_ad_content_rating="G")
```

That also makes every request non-personalized. Do not ask for tracking, set `removeAdIdPermission` in the plugin,
and in AdMob mark the app as directed to children and use only Families-certified mediation partners.

## Platforms

| Target | Ads |
| --- | --- |
| Android (API 24+) | AdMob, all types |
| iOS (13+) | AdMob, all types |
| Mac Catalyst, Windows | none (Google has no SDK); `is_available()` is False |
| Web page, desktop window (`pyxpo run web` / `desktop`) | simulated test ads for `TestIds` only, with the same events and results, so ad flows can be tested |

## Troubleshooting

- Ads fail with `network` on the Android emulator while the browser works: the emulator may only reach the network
  over IPv6 through the host. Run the emulator with a host proxy (`adb shell settings put global http_proxy
  10.0.2.2:PORT`) and clear it afterwards (`settings delete global http_proxy`).
- `invalid_request` once, right after the first install on the iOS simulator: the SDK is still setting up; the next
  load works.
- The "AdMob native ad validator" popup on test native ads lists layout problems; "No implementation issues found"
  is the goal. The template views here pass it.
