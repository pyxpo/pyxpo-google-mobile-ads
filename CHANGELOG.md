# Changelog

## 0.1.0a1

- First release: banner, interstitial, rewarded, rewarded interstitial, app open and native template ads on Android
  and iOS (Google Mobile Ads SDK), consent through Google's User Messaging Platform, request configuration
  (child-directed, under age of consent, content rating, test devices), simulated test ads on the web page and
  desktop window, and the config plugin `pyxpo_google_mobile_ads.plugin`.
- Ads wait for `initialize_async()`: no request goes out before the SDK has the request configuration, and banners
  and native ads load once.
- Needs `pyxpo>=0.1.0a3` (native contract 3, `ios.swiftPackages`).
