import UIKit
#if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)
import AppTrackingTransparency
import GoogleMobileAds
import UserMessagingPlatform
#endif

/// pyxpo-google-mobile-ads on iOS: the Google Mobile Ads SDK (v12 Swift names). Full-screen ads live here by the id Python gives
/// them (`load` keeps the SDK ad, `show` uses it once and lets it go); events go to Python as
/// `GoogleMobileAds.event {id, event, data}`. Consent is Google's User Messaging Platform. SDK errors are reply values
/// (`{error, message, code, domain}`), so Python can tell no fill from a broken call.
/// Mac Catalyst links no ads SDK (the Swift packages are iOS only): `available` is false and Python never calls in.
final class GoogleMobileAdsModule: PxViewModule {
    static let moduleName = "GoogleMobileAds"
    static let viewTypes = ["PxBannerAd", "PxNativeAd"]
    #if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)
    static var constants: [String: Any] { ["available": true] }
    private var ads: [Int: AnyObject] = [:]
    private var shows: [Int: AdShow] = [:]
    #else
    static var constants: [String: Any] { ["available": false] }
    #endif

    init() {}

    func makeView(_ ctx: PxModuleContext, _ type: String, _ emit: @escaping (String, Any?) -> Void) -> PxNativeView {
        #if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)
        return type == "PxBannerAd" ? PxBannerAdView(ctx, emit) : PxNativeAdView(ctx, emit)
        #else
        return PxNoAdView()
        #endif
    }

    func call(_ ctx: PxModuleContext, _ method: String, _ args: [Any], _ reply: @escaping PxReply) {
        #if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)
        let o = args.first as? [String: Any] ?? [:]
        switch method {
        case "initialize":
            Self.configure(o)
            MobileAds.shared.start { status in
                var out: [String: Any] = [:]
                for (name, s) in status.adapterStatusesByClassName {
                    out[name] = ["state": s.state == .ready ? "ready" : "not_ready", "description": s.description,
                                 "latency": s.latency]
                }
                reply(out, nil)
            }
        case "setRequestConfiguration": Self.configure(o); reply(nil, nil)
        case "requestTracking":
            ATTrackingManager.requestTrackingAuthorization { s in reply(Self.tracking(s), nil) }
        case "trackingStatus": reply(Self.tracking(ATTrackingManager.trackingAuthorizationStatus), nil)
        case "openAdInspector":
            guard let vc = ctx.presenter() else { reply(["error": "inspector", "message": "no view controller"], nil); return }
            MobileAds.shared.presentAdInspector(from: vc) { e in reply(e.map(Self.nsError("inspector")) ?? [:], nil) }
        case "load": load(ctx, o, reply)
        case "show": show(ctx, o, reply)
        case "destroy": ads[o["id"] as? Int ?? 0] = nil; reply(nil, nil)
        case "consentRequestInfo": consentRequest(o, reply)
        case "consentShowFormIfRequired":
            guard let vc = ctx.presenter() else { reply(["error": "consent", "message": "no view controller"], nil); return }
            ConsentForm.loadAndPresentIfRequired(from: vc) { e in reply(e.map(Self.nsError("consent")) ?? Self.consentInfo(), nil) }
        case "consentShowPrivacyOptionsForm":
            guard let vc = ctx.presenter() else { reply(["error": "consent", "message": "no view controller"], nil); return }
            ConsentForm.presentPrivacyOptionsForm(from: vc) { e in reply(e.map(Self.nsError("consent")) ?? Self.consentInfo(), nil) }
        case "consentReset": ConsentInformation.shared.reset(); reply(nil, nil)
        default: reply(nil, "GoogleMobileAds has no method \(method)")
        }
        #else
        reply(nil, "ads are not available on this platform")
        #endif
    }

    #if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)
    // -- setup ------------------------------------------------------------------------------------------------

    private static func tri(_ o: [String: Any], _ key: String) -> NSNumber? {
        (o[key] as? Bool).map { NSNumber(value: $0) }
    }

    static func configure(_ o: [String: Any]) {
        let c = MobileAds.shared.requestConfiguration
        if o.keys.contains("testDeviceIds") { c.testDeviceIdentifiers = (o["testDeviceIds"] as? [String]) ?? [] }
        if o.keys.contains("tagForChildDirectedTreatment") { c.tagForChildDirectedTreatment = tri(o, "tagForChildDirectedTreatment") }
        if o.keys.contains("tagForUnderAgeOfConsent") { c.tagForUnderAgeOfConsent = tri(o, "tagForUnderAgeOfConsent") }
        if o.keys.contains("maxAdContentRating") {
            switch o["maxAdContentRating"] as? String {
            case "G": c.maxAdContentRating = .general
            case "PG": c.maxAdContentRating = .parentalGuidance
            case "T": c.maxAdContentRating = .teen
            case "MA": c.maxAdContentRating = .matureAudience
            default: c.maxAdContentRating = nil
            }
        }
    }

    private static func tracking(_ s: ATTrackingManager.AuthorizationStatus) -> String {
        switch s {
        case .authorized: return "authorized"
        case .denied: return "denied"
        case .restricted: return "restricted"
        default: return "not_determined"
        }
    }

    static func nsError(_ kind: String) -> (Error) -> [String: Any] {
        { e in let n = e as NSError; return ["error": kind, "message": n.localizedDescription, "code": n.code, "domain": n.domain] }
    }

    // GADErrorCode: 0 invalid request, 1 no fill, 2 network, 5 timeout, 9 mediation no fill, 12 invalid argument,
    // 20 application identifier missing.
    static func loadError(_ e: Error) -> [String: Any] {
        let n = e as NSError
        let code: String
        switch n.code {
        case 1, 9: code = "no_fill"
        case 2: code = "network"
        case 5: code = "timeout"
        case 0, 12, 13: code = "invalid_request"
        case 20: code = "app_id_missing"
        default: code = "internal"
        }
        return ["error": code, "message": n.localizedDescription, "code": n.code, "domain": n.domain]
    }

    // GADPresentationErrorCode: 15 not ready, 18 and 19 already used.
    static func showError(_ e: Error) -> [String: Any] {
        let n = e as NSError
        let code: String
        switch n.code {
        case 15: code = "not_ready"
        case 18, 19: code = "already_used"
        default: code = "internal"
        }
        return ["error": code, "message": n.localizedDescription, "code": n.code, "domain": n.domain]
    }

    static func paid(_ v: AdValue) -> [String: Any] {
        let p: String
        switch v.precision {
        case .estimated: p = "estimated"
        case .publisherProvided: p = "publisher_provided"
        case .precise: p = "precise"
        default: p = "unknown"
        }
        // AdValue.value is in currency units; Python gets micros, like Android.
        return ["valueMicros": v.value.multiplying(byPowerOf10: 6).int64Value, "currencyCode": v.currencyCode, "precision": p]
    }

    static func request(_ o: [String: Any]?) -> Request {
        let r = Request()
        guard let o = o else { return r }
        if let k = o["keywords"] as? [String], !k.isEmpty { r.keywords = k }
        if let u = o["contentUrl"] as? String, !u.isEmpty { r.contentURL = u }
        if let n = o["neighboringContentUrls"] as? [String], !n.isEmpty { r.neighboringContentURLs = n }
        if o["nonPersonalized"] as? Bool == true {
            let extras = Extras()
            extras.additionalParameters = ["npa": "1"]
            r.register(extras)
        }
        return r
    }

    // -- full-screen ads ----------------------------------------------------------------------------------------

    private func load(_ ctx: PxModuleContext, _ o: [String: Any], _ reply: @escaping PxReply) {
        let id = o["id"] as? Int ?? 0
        let unit = o["unitId"] as? String ?? ""
        let req = Self.request(o["request"] as? [String: Any])
        let ssv: ServerSideVerificationOptions? = (o["serverSideVerification"] as? [String: Any]).map {
            let s = ServerSideVerificationOptions()
            s.userIdentifier = $0["userId"] as? String
            s.customRewardText = $0["customData"] as? String
            return s
        }
        let paid: (AdValue) -> Void = { [weak ctx] v in ctx?.emit("GoogleMobileAds.event", ["id": id, "event": "paid", "data": Self.paid(v)]) }
        let done: (AnyObject?, Error?) -> Void = { [weak self] ad, e in
            if let ad = ad { self?.ads[id] = ad; reply([String: Any](), nil) }
            else { reply(Self.loadError(e ?? NSError(domain: "pyxpo.ads", code: -1)), nil) }
        }
        switch o["type"] as? String {
        case "interstitial":
            InterstitialAd.load(with: unit, request: req) { ad, e in ad?.paidEventHandler = paid; done(ad, e) }
        case "rewarded":
            RewardedAd.load(with: unit, request: req) { ad, e in
                ad?.paidEventHandler = paid
                if let s = ssv { ad?.serverSideVerificationOptions = s }
                done(ad, e)
            }
        case "rewarded_interstitial":
            RewardedInterstitialAd.load(with: unit, request: req) { ad, e in
                ad?.paidEventHandler = paid
                if let s = ssv { ad?.serverSideVerificationOptions = s }
                done(ad, e)
            }
        case "app_open":
            AppOpenAd.load(with: unit, request: req) { ad, e in ad?.paidEventHandler = paid; done(ad, e) }
        default: reply(nil, "unknown ad type \(o["type"] ?? "")")
        }
    }

    private func show(_ ctx: PxModuleContext, _ o: [String: Any], _ reply: @escaping PxReply) {
        let id = o["id"] as? Int ?? 0
        guard let ad = ads.removeValue(forKey: id) else {
            reply(["shown": false, "error": "not_loaded", "message": "no loaded ad with id \(id)"], nil); return
        }
        guard let vc = ctx.presenter() else {
            reply(["shown": false, "error": "app_not_foreground", "message": "no view controller to show from"], nil); return
        }
        let s = AdShow(id, ctx) { [weak self] result in self?.shows[id] = nil; reply(result, nil) }
        shows[id] = s  // the SDK holds its delegate weakly
        let earned: (String, NSDecimalNumber) -> Void = { type, amount in
            s.reward = ["type": type, "amount": amount.intValue]
            s.event("earned_reward", s.reward)
        }
        switch ad {
        case let a as InterstitialAd: a.fullScreenContentDelegate = s; a.present(from: vc)
        case let a as RewardedAd:
            a.fullScreenContentDelegate = s
            a.present(from: vc) { earned(a.adReward.type, a.adReward.amount) }
        case let a as RewardedInterstitialAd:
            a.fullScreenContentDelegate = s
            a.present(from: vc) { earned(a.adReward.type, a.adReward.amount) }
        case let a as AppOpenAd: a.fullScreenContentDelegate = s; a.present(from: vc)
        default: s.finish(["shown": false, "error": "internal", "message": "unknown ad object"])
        }
    }

    // -- consent (User Messaging Platform) ------------------------------------------------------------------------

    private func consentRequest(_ o: [String: Any], _ reply: @escaping PxReply) {
        let p = RequestParameters()
        p.isTaggedForUnderAgeOfConsent = o["tagForUnderAgeOfConsent"] as? Bool ?? false
        let geo = o["debugGeography"] as? String ?? ""
        let ids = (o["testDeviceIds"] as? [String]) ?? []
        if !geo.isEmpty || !ids.isEmpty {
            let d = DebugSettings()
            switch geo {
            case "eea": d.geography = .EEA
            case "regulated_us_state": d.geography = .regulatedUSState
            case "other": d.geography = .other
            case "disabled": d.geography = .disabled
            default: break
            }
            d.testDeviceIdentifiers = ids
            p.debugSettings = d
        }
        ConsentInformation.shared.requestConsentInfoUpdate(with: p) { e in
            reply(e.map(Self.nsError("consent")) ?? Self.consentInfo(), nil)
        }
    }

    static func consentInfo() -> [String: Any] {
        let i = ConsentInformation.shared
        let status: String
        switch i.consentStatus {
        case .required: status = "required"
        case .notRequired: status = "not_required"
        case .obtained: status = "obtained"
        default: status = "unknown"
        }
        return ["status": status, "canRequestAds": i.canRequestAds,
                "privacyOptionsRequired": i.privacyOptionsRequirementStatus == .required,
                "formAvailable": i.formStatus == .available]
    }
    #endif
}

#if canImport(GoogleMobileAds) && !targetEnvironment(macCatalyst)
/// One show of a full-screen ad: forwards the SDK's delegate calls as events and replies once, at dismiss or failure.
final class AdShow: NSObject, FullScreenContentDelegate {
    let id: Int
    weak var ctx: PxModuleContext?
    var reward: [String: Any]?
    private var reply: (([String: Any]) -> Void)?

    init(_ id: Int, _ ctx: PxModuleContext, _ reply: @escaping ([String: Any]) -> Void) {
        self.id = id
        self.ctx = ctx
        self.reply = reply
    }

    func event(_ name: String, _ data: Any? = nil) {
        ctx?.emit("GoogleMobileAds.event", ["id": id, "event": name, "data": data ?? NSNull()])
    }

    func finish(_ result: [String: Any]) {
        let r = reply
        reply = nil
        r?(result)
    }

    func adWillPresentFullScreenContent(_ ad: FullScreenPresentingAd) { event("opened") }
    func adDidRecordImpression(_ ad: FullScreenPresentingAd) { event("impression") }
    func adDidRecordClick(_ ad: FullScreenPresentingAd) { event("clicked") }
    func ad(_ ad: FullScreenPresentingAd, didFailToPresentFullScreenContentWithError error: Error) {
        var err = GoogleMobileAdsModule.showError(error)
        event("show_failed", err)
        err["shown"] = false
        finish(err)
    }
    func adDidDismissFullScreenContent(_ ad: FullScreenPresentingAd) {
        event("closed")
        finish(["shown": true, "reward": reward ?? NSNull()])
    }
}

/// A container that tells its owner when its width changes (adaptive banners need the width to pick a size).
final class AdContainer: UIView {
    var onWidth: ((CGFloat) -> Void)?
    private var lastWidth: CGFloat = 0
    override func layoutSubviews() {
        super.layoutSubviews()
        if bounds.width > 0 && bounds.width != lastWidth {
            lastWidth = bounds.width
            onWidth?(bounds.width)
        }
        for v in subviews { v.frame.origin.x = max(0, (bounds.width - v.frame.width) / 2) }
    }

    /// Takes the ad's height now: Python sets the same height a moment later, but the SDK checks the ad's visible
    /// size as soon as it shows (a 0-high box reads as an ad under 32x32 points).
    func fit(_ height: CGFloat) {
        if bounds.height < height { frame.size.height = height }
    }
}

private func str(_ p: [String: Any], _ k: String) -> String { p[k] as? String ?? "" }

/// `PxBannerAd`: a BannerView. Adaptive sizes wait for the box's width; the height goes to Python (`size`) once an ad
/// loads, so a banner with no fill stays 0 high. A unit, size or request change makes a new BannerView.
final class PxBannerAdView: NSObject, PxNativeView, BannerViewDelegate {
    private let box = AdContainer()
    var view: UIView { box }
    private weak var ctx: PxModuleContext?
    private let emit: (String, Any?) -> Void
    private var props: [String: Any] = [:]
    private var banner: BannerView?
    private var builtFor = ""

    init(_ ctx: PxModuleContext, _ emit: @escaping (String, Any?) -> Void) {
        self.ctx = ctx
        self.emit = emit
        super.init()
        box.clipsToBounds = true
        box.onWidth = { [weak self] _ in self?.relayout() }
    }

    func update(_ props: [String: Any], _ removed: [String]) {
        for (k, v) in props { self.props[k] = v }
        for k in removed { self.props[k] = nil }
        relayout()
    }

    private func adSize(_ width: CGFloat) -> AdSize? {
        let size = str(props, "size")
        switch size {
        case "ANCHORED_ADAPTIVE_BANNER": return width > 0 ? currentOrientationAnchoredAdaptiveBanner(width: width) : nil
        case "INLINE_ADAPTIVE_BANNER":
            guard width > 0 else { return nil }
            if let mh = props["max_height"] as? Int { return inlineAdaptiveBanner(width: width, maxHeight: CGFloat(mh)) }
            return currentOrientationInlineAdaptiveBanner(width: width)
        case "BANNER": return AdSizeBanner
        case "LARGE_BANNER": return AdSizeLargeBanner
        case "MEDIUM_RECTANGLE": return AdSizeMediumRectangle
        case "FULL_BANNER": return AdSizeFullBanner
        case "LEADERBOARD": return AdSizeLeaderboard
        default:
            let parts = size.split(separator: "x").compactMap { Double($0) }
            return parts.count == 2 ? adSizeFor(cgSize: CGSize(width: parts[0], height: parts[1])) : nil
        }
    }

    private func relayout() {
        let unit = str(props, "unit_id")
        guard !unit.isEmpty else { return }
        let adaptive = str(props, "size").hasSuffix("_ADAPTIVE_BANNER")
        let width = box.bounds.width
        let request = (try? JSONSerialization.data(withJSONObject: props["request"] ?? [:], options: [.sortedKeys]))
            .flatMap { String(data: $0, encoding: .utf8) } ?? ""
        let key = [unit, str(props, "size"), "\(props["max_height"] ?? "")", request, adaptive ? "\(Int(width))" : "0"]
            .joined(separator: "|")
        guard key != builtFor, let size = adSize(width) else { return }
        builtFor = key
        banner?.removeFromSuperview()
        let b = BannerView(adSize: size)
        b.adUnitID = unit
        b.rootViewController = ctx?.presenter()
        b.delegate = self
        b.paidEventHandler = { [weak self] v in self?.emit("paid", GoogleMobileAdsModule.paid(v)) }
        b.frame = CGRect(origin: .zero, size: size.size)
        box.addSubview(b)
        banner = b
        b.load(GoogleMobileAdsModule.request(props["request"] as? [String: Any]))
    }

    func bannerViewDidReceiveAd(_ bannerView: BannerView) {
        let s = bannerView.adSize.size
        bannerView.frame.size = s
        box.fit(s.height)
        box.setNeedsLayout()
        let dims: [String: Any] = ["width": Double(s.width), "height": Double(s.height)]
        emit("size", dims)
        emit("load", dims)
    }
    func bannerView(_ bannerView: BannerView, didFailToReceiveAdWithError error: Error) { emit("error", GoogleMobileAdsModule.loadError(error)) }
    func bannerViewDidRecordImpression(_ bannerView: BannerView) { emit("impression", nil) }
    func bannerViewDidRecordClick(_ bannerView: BannerView) { emit("click", nil) }
    func bannerViewWillPresentScreen(_ bannerView: BannerView) { emit("open", nil) }
    func bannerViewDidDismissScreen(_ bannerView: BannerView) { emit("close", nil) }

    func dispose() {
        banner?.delegate = nil
        banner?.removeFromSuperview()
        banner = nil
    }
}

/// `PxNativeAd`: Google's small or medium template built in code around a NativeAdView (the SDK adds the AdChoices
/// icon). Once an ad loads, the template's height at the box's width goes to Python (`size`).
final class PxNativeAdView: NSObject, PxNativeView, NativeAdLoaderDelegate, NativeAdDelegate {
    private let box = AdContainer()
    var view: UIView { box }
    private weak var ctx: PxModuleContext?
    private let emit: (String, Any?) -> Void
    private var props: [String: Any] = [:]
    private var loader: AdLoader?
    private var current: NativeAd?
    private var shown: NativeAdView?
    private var builtFor = ""
    private var drawnLook = ""

    init(_ ctx: PxModuleContext, _ emit: @escaping (String, Any?) -> Void) {
        self.ctx = ctx
        self.emit = emit
        super.init()
        box.onWidth = { [weak self] w in self?.measure(w) }
    }

    func update(_ props: [String: Any], _ removed: [String]) {
        for (k, v) in props { self.props[k] = v }
        for k in removed { self.props[k] = nil }
        let unit = str(self.props, "unit_id")
        let request = (try? JSONSerialization.data(withJSONObject: self.props["request"] ?? [:], options: [.sortedKeys]))
            .flatMap { String(data: $0, encoding: .utf8) } ?? ""
        let key = unit + "|" + request
        if !unit.isEmpty && key != builtFor {
            builtFor = key
            let l = AdLoader(adUnitID: unit, rootViewController: ctx?.presenter(), adTypes: [.native], options: nil)
            l.delegate = self
            loader = l
            l.load(GoogleMobileAdsModule.request(self.props["request"] as? [String: Any]))
        } else if let ad = current, look() != drawnLook {
            render(ad)  // a color or template change
        }
    }

    /// The props the template is drawn from. Other updates (Python re-renders with new callbacks) keep the view: a
    /// fresh NativeAdView would re-register the ad with asset views not laid out yet.
    private func look() -> String {
        ["template", "background_color", "text_color", "button_color", "button_text_color"]
            .map { "\(props[$0] ?? "")" }.joined(separator: "|")
    }

    func adLoader(_ adLoader: AdLoader, didReceive nativeAd: NativeAd) {
        guard adLoader === loader else { return }
        current = nativeAd
        nativeAd.delegate = self
        nativeAd.paidEventHandler = { [weak self] v in self?.emit("paid", GoogleMobileAdsModule.paid(v)) }
        render(nativeAd)
        emit("load", nil)
    }
    func adLoader(_ adLoader: AdLoader, didFailToReceiveAdWithError error: Error) {
        if adLoader === loader { emit("error", GoogleMobileAdsModule.loadError(error)) }
    }
    func nativeAdDidRecordImpression(_ nativeAd: NativeAd) { emit("impression", nil) }
    func nativeAdDidRecordClick(_ nativeAd: NativeAd) { emit("click", nil) }
    func nativeAdWillPresentScreen(_ nativeAd: NativeAd) { emit("open", nil) }
    func nativeAdDidDismissScreen(_ nativeAd: NativeAd) { emit("close", nil) }

    private func color(_ key: String, _ fallback: UIColor) -> UIColor {
        guard let n = props[key] as? NSNumber else { return fallback }
        let v = UInt32(truncatingIfNeeded: n.int64Value)  // 0xAARRGGBB
        return UIColor(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
                       blue: CGFloat(v & 0xFF) / 255, alpha: CGFloat((v >> 24) & 0xFF) / 255)
    }

    private func render(_ ad: NativeAd) {
        drawnLook = look()
        let medium = str(props, "template") != "small"
        let text = color("text_color", UIColor(white: 0.13, alpha: 1))
        let nav = NativeAdView()
        nav.backgroundColor = color("background_color", .white)

        let badge = UILabel()
        badge.text = "Ad"
        badge.textAlignment = .center
        badge.font = .boldSystemFont(ofSize: 11)
        badge.textColor = .white
        badge.backgroundColor = UIColor(red: 0.98, green: 0.67, blue: 0, alpha: 1)
        badge.layer.cornerRadius = 3
        badge.clipsToBounds = true
        badge.setContentHuggingPriority(.required, for: .horizontal)
        badge.widthAnchor.constraint(equalToConstant: 24).isActive = true
        badge.heightAnchor.constraint(equalToConstant: 16).isActive = true
        let headline = UILabel()
        headline.text = ad.headline
        headline.font = .boldSystemFont(ofSize: 15)
        headline.textColor = text
        headline.numberOfLines = medium ? 2 : 1
        let sub = UILabel()
        sub.text = medium ? ad.advertiser : ad.body
        sub.font = .systemFont(ofSize: 12)
        sub.textColor = text.withAlphaComponent(0.7)
        sub.isHidden = (sub.text ?? "").isEmpty
        let icon = UIImageView(image: ad.icon?.image)
        icon.contentMode = .scaleAspectFill
        icon.clipsToBounds = true
        icon.layer.cornerRadius = 6
        icon.isHidden = ad.icon == nil
        let side: CGFloat = medium ? 40 : 48
        icon.widthAnchor.constraint(equalToConstant: side).isActive = true
        icon.heightAnchor.constraint(equalToConstant: side).isActive = true
        let cta = UIButton(type: .system)
        cta.setTitle(ad.callToAction, for: .normal)
        cta.setTitleColor(color("button_text_color", .white), for: .normal)
        cta.backgroundColor = color("button_color", UIColor(red: 0.1, green: 0.45, blue: 0.91, alpha: 1))
        cta.layer.cornerRadius = 8
        cta.titleLabel?.font = .systemFont(ofSize: 14, weight: .semibold)
        cta.contentEdgeInsets = UIEdgeInsets(top: 8, left: 14, bottom: 8, right: 14)
        cta.isUserInteractionEnabled = false  // the SDK handles taps on the asset views
        cta.isHidden = (ad.callToAction ?? "").isEmpty

        let titleRow = UIStackView(arrangedSubviews: [badge, headline])
        titleRow.spacing = 6
        titleRow.alignment = .center
        let texts = UIStackView(arrangedSubviews: [titleRow, sub])
        texts.axis = .vertical
        texts.spacing = 2
        let top = UIStackView(arrangedSubviews: medium ? [icon, texts] : [icon, texts, cta])
        top.spacing = 10
        top.alignment = .center
        let col = UIStackView(arrangedSubviews: [top])
        col.axis = .vertical
        col.spacing = 8
        col.translatesAutoresizingMaskIntoConstraints = false
        nav.iconView = icon
        nav.headlineView = headline
        nav.callToActionView = cta
        if medium {
            // AdMob: the media view is at least 120x120 points.
            let media = MediaView()
            media.mediaContent = ad.mediaContent
            media.heightAnchor.constraint(equalToConstant: 180).isActive = true
            let body = UILabel()
            body.text = ad.body
            body.font = .systemFont(ofSize: 13)
            body.textColor = text
            body.numberOfLines = 2
            body.isHidden = (ad.body ?? "").isEmpty
            col.addArrangedSubview(media)
            col.addArrangedSubview(body)
            col.addArrangedSubview(cta)
            nav.mediaView = media
            nav.bodyView = body
            if !(ad.advertiser ?? "").isEmpty { nav.advertiserView = sub }
        } else if !(ad.body ?? "").isEmpty {
            nav.bodyView = sub
        }
        nav.addSubview(col)
        NSLayoutConstraint.activate([
            col.leadingAnchor.constraint(equalTo: nav.leadingAnchor, constant: 10),
            col.trailingAnchor.constraint(equalTo: nav.trailingAnchor, constant: -10),
            col.topAnchor.constraint(equalTo: nav.topAnchor, constant: 8),
        ])

        shown?.removeFromSuperview()
        shown = nav
        box.addSubview(nav)
        if box.bounds.width > 0 { measure(box.bounds.width) }
    }

    /// Sizes the template at this width and sends its height to Python.
    private func measure(_ width: CGFloat) {
        guard let nav = shown, let col = nav.subviews.first else { return }
        let h = col.systemLayoutSizeFitting(CGSize(width: width - 20, height: UIView.layoutFittingCompressedSize.height),
                                            withHorizontalFittingPriority: .required,
                                            verticalFittingPriority: .fittingSizeLevel).height + 18
        nav.frame = CGRect(x: 0, y: 0, width: width, height: h)
        box.fit(ceil(h))
        // Registered only once the asset views have their frames: the SDK checks the ad's size and attribution then.
        if nav.nativeAd == nil, let ad = current {
            nav.layoutIfNeeded()
            nav.nativeAd = ad
        }
        emit("size", ["width": Double(width), "height": Double(ceil(h))])
    }

    func dispose() {
        loader?.delegate = nil
        loader = nil
        current = nil
        shown?.removeFromSuperview()
        shown = nil
    }
}
#else
/// Mac Catalyst: no ads SDK. Python renders no ad views where `available` is false; this only answers a stray mount.
final class PxNoAdView: PxNativeView {
    let view = UIView()
    func update(_ props: [String: Any], _ removed: [String]) {}
}
#endif
