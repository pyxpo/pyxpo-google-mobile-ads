# pyxpo-google-mobile-ads plan

Ads module for pyxpo apps: `pip install pyxpo-google-mobile-ads`, `import pyxpo_google_mobile_ads as Ads`, `modules = ["google-mobile-ads"]`.
Backed by the Google Mobile Ads SDK (AdMob) on Android and iOS. The API follows react-native-google-mobile-ads,
in pyxpo's Python style. First user: doodle-dance (an ad when Generate is pressed).

## Decisions (user, 2026-10-18)

- Generate shows an **interstitial**, with a frequency cap.
- Audience includes **children**: `tag_for_child_directed_treatment=True`, `max_ad_content_rating="G"`,
  non-personalized ads. One app setting switches this off later.
- **Banner on Gallery** only.
- SDK downloads approved: Gradle for Android, SPM for iOS. Only Google test ad IDs while testing.

## Platforms

| Target | Ads | Notes |
|---|---|---|
| Android | AdMob (play-services-ads 24.x + UMP 3.x) | full support |
| iOS | AdMob (GoogleMobileAds SPM package + UMP) | needs swiftPackages in modules (F1) |
| Mac Catalyst, Windows, desktop | none | Google has no SDK there. `Ads.is_available()` is False, `show_async()` returns "not shown", so the app flow carries on |
| Web | simulated test ads in dev | same events and results, so flows are testable. Real web ads (AdSense or Ad Manager) are a later step |

## Ad types

- **Banner**: `Ads.BannerAd(unit_id, size=...)` component. Sizes: anchored adaptive (default), inline adaptive,
  BANNER 320x50, LARGE_BANNER 320x100, MEDIUM_RECTANGLE 300x250, FULL_BANNER, LEADERBOARD. Events: load, error,
  open, close, click, impression, paid.
- **Interstitial**: full screen, skippable.
- **Rewarded**: full screen; the user gets a reward (`type`, `amount`) after watching.
- **Rewarded interstitial**: full screen with a reward, shown without opt-in.
- **App open**: full screen shown when the app comes to the front (with a cooldown).
- **Native**: `Ads.NativeAdView(unit_id, template="small"|"medium", colors...)`, drawn natively from Google's
  template layout (headline, body, icon, media, call to action, "Ad" badge), so it follows AdMob policy.

Full-screen ads share one class shape:

```python
ad = Ads.InterstitialAd(Ads.TestIds.INTERSTITIAL)
await ad.load_async()              # raises AdError(code, message) on no-fill or network error
result = await ad.show_async()     # returns after the ad is closed
# result.shown, result.error, result.reward (rewarded types: type, amount; None if not earned)
ad.add_listener("clicked", fn)     # loaded, failed, opened, impression, clicked, closed, earned_reward, paid
```

Hooks: `use_interstitial_ad(unit_id, preload=True)` and `use_rewarded_ad(...)` give `{loaded, loading, error,
show_async, load}` and reload after close. An ad object is one-shot as in the SDK; the hooks hide that.

## Setup and privacy

- `await Ads.initialize_async()` once at start, after consent.
- Consent (Google UMP, needed for EEA/UK): `Ads.consent.request_info_async()`,
  `show_form_if_required_async()`, `can_request_ads()`, `show_privacy_options_form_async()`.
- iOS App Tracking Transparency: `Ads.request_tracking_async()`, Info.plist text set by the plugin.
- Request configuration: `test_device_ids`, `tag_for_child_directed_treatment`, `tag_for_under_age_of_consent`,
  `max_ad_content_rating` (G, PG, T, MA). Per request: `keywords`, `content_url`, `non_personalized`.
- `Ads.TestIds.*`: Google's official test unit IDs. A debug build uses them unless the app passes its own.
- Config plugin `pyxpo_google_mobile_ads.plugin` with options `androidAppId`, `iosAppId` (defaults: Google's test app IDs),
  `userTrackingUsageDescription`, `skAdNetworkItems` (default: Google's list), `delayAppMeasurementInit`.
  It writes the Android manifest `com.google.android.gms.ads.APPLICATION_ID` meta-data and the Info.plist
  `GADApplicationIdentifier`, `SKAdNetworkItems` and `NSUserTrackingUsageDescription` keys.
  App IDs are not secret, but real ones go in pyproject.toml, not in code.

## Framework work (pyxpo)

- **F1** module manifest `ios.swiftPackages: [{url, version, products, platformFilter}]` goes into project.yml
  packages and target dependencies. `platformFilter: ios` keeps GoogleMobileAds out of the Catalyst build.
  Swift sources use `#if canImport(GoogleMobileAds)`.
- **F2** anything else found while building, logged in pyxpo-apps/ISSUES.md.

## Steps

1. F1 swiftPackages: autolink, project.yml render, validation, and tests.
2. Package skeleton (pyproject, manifest, README, CHANGELOG) plus the Python API: types, validation, ad state
   machine, listeners, hooks, consent, request config, TestIds. Tests with a fake bridge.
3. Config plugin and its tests (manifest XML, Info.plist dict, option errors).
4. Android Kotlin: initialize, consent, four full-screen types, banner view, native template view, paid events.
5. iOS Swift: the same, compiled out on Catalyst.
6. web.js simulated ads (overlay with countdown and close, test banner box) and node tests.
7. doodle-dance: interstitial on Generate. Generate never waits more than ~1.5 s for an ad, there is a frequency
   cap (at most one ad every N generates or M minutes), the next ad preloads, and the app falls back to no ad on
   error, offline or desktop. Also an optional banner on Gallery. Tests for the cap and fallback logic.
8. End to end:
   - Android emulator with Google test ads (banner "Test Ad", interstitial close returns to Animate, rewarded
     gives the reward, consent form in the EEA debug geography).
   - Web simulated ads.
   - Mac: no ads, flow unchanged.
   - iOS simulator: a small sample app (doodle-dance on iOS waits for numpy).
9. Review the whole diff, write docs, commit and push, and update ISSUES and PROGRESS. Publishing to PyPI needs the user's OK.

## Tests

- pytest, Python 3.9 compatible:
  - API argument checks.
  - Load/show state: show before load, show twice, load while loading.
  - Event routing per ad id, and that the hooks reload.
  - Consent flow.
  - Request config mapping.
  - Plugin output.
  - Manifest validation and the swiftPackages render.
  - doodle-dance cap and fallback.
- node: the web.js simulated ad lifecycle.
- String checks on the Kotlin and Swift sources (registered events, Catalyst guard), plus real Gradle and Xcode
  builds in step 8.
