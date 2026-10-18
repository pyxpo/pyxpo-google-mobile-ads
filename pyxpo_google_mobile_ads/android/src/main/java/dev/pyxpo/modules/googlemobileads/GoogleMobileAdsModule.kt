package dev.pyxpo.modules.googlemobileads

import android.content.Context
import android.os.Bundle
import com.google.ads.mediation.admob.AdMobAdapter
import com.google.android.gms.ads.AdError
import com.google.android.gms.ads.AdRequest
import com.google.android.gms.ads.AdValue
import com.google.android.gms.ads.FullScreenContentCallback
import com.google.android.gms.ads.LoadAdError
import com.google.android.gms.ads.MobileAds
import com.google.android.gms.ads.OnUserEarnedRewardListener
import com.google.android.gms.ads.RequestConfiguration
import com.google.android.gms.ads.appopen.AppOpenAd
import com.google.android.gms.ads.initialization.AdapterStatus
import com.google.android.gms.ads.interstitial.InterstitialAd
import com.google.android.gms.ads.interstitial.InterstitialAdLoadCallback
import com.google.android.gms.ads.rewarded.RewardedAd
import com.google.android.gms.ads.rewarded.RewardedAdLoadCallback
import com.google.android.gms.ads.rewarded.ServerSideVerificationOptions
import com.google.android.gms.ads.rewardedinterstitial.RewardedInterstitialAd
import com.google.android.gms.ads.rewardedinterstitial.RewardedInterstitialAdLoadCallback
import com.google.android.ump.ConsentDebugSettings
import com.google.android.ump.ConsentInformation
import com.google.android.ump.ConsentRequestParameters
import com.google.android.ump.FormError
import com.google.android.ump.UserMessagingPlatform
import dev.pyxpo.moduleapi.PxModule
import dev.pyxpo.moduleapi.PxModuleContext
import dev.pyxpo.moduleapi.PxNativeView
import dev.pyxpo.moduleapi.PxReply
import org.json.JSONArray
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * pyxpo-google-mobile-ads on Android: the Google Mobile Ads SDK. Full-screen ads live here by the id Python gives them
 * (`load` keeps the SDK ad, `show` uses it once and lets it go); events go to Python as
 * `GoogleMobileAds.event {id, event, data}`. Consent is Google's User Messaging Platform. The views are in GoogleMobileAdsViews.kt.
 * SDK errors are reply values (`{error, message, code, domain}`), so Python can tell no fill from a broken call.
 */
class GoogleMobileAdsModule : PxModule() {
    override val name = "GoogleMobileAds"
    override val viewTypes = setOf("PxBannerAd", "PxNativeAd")

    private val ads = HashMap<Int, Any>()

    override fun constants(context: Context): Map<String, Any?> = mapOf("available" to true)

    override fun makeView(ctx: PxModuleContext, type: String, emit: (name: String, value: Any?) -> Unit): PxNativeView =
        if (type == "PxBannerAd") BannerAdView(ctx.activity, emit) else NativeAdBox(ctx.activity, emit)

    override fun call(ctx: PxModuleContext, method: String, args: JSONArray, reply: PxReply) {
        val o = args.optJSONObject(0) ?: JSONObject()
        when (method) {
            "initialize" -> initialize(ctx, o, reply)
            "setRequestConfiguration" -> { configure(o); reply(null, null) }
            "load" -> load(ctx, o, reply)
            "show" -> show(ctx, o, reply)
            "destroy" -> { ads.remove(o.optInt("id")); reply(null, null) }
            "openAdInspector" -> MobileAds.openAdInspector(ctx.activity) { e ->
                reply(if (e == null) emptyMap<String, Any?>() else
                    mapOf("error" to "inspector", "message" to e.message, "code" to e.code, "domain" to e.domain), null)
            }
            "consentRequestInfo" -> consentRequest(ctx, o, reply)
            "consentShowFormIfRequired" -> UserMessagingPlatform.loadAndShowConsentFormIfRequired(ctx.activity) { e ->
                reply(e?.let(::formError) ?: consentInfo(ctx.activity), null)
            }
            "consentShowPrivacyOptionsForm" -> UserMessagingPlatform.showPrivacyOptionsForm(ctx.activity) { e ->
                reply(e?.let(::formError) ?: consentInfo(ctx.activity), null)
            }
            "consentReset" -> { UserMessagingPlatform.getConsentInformation(ctx.activity).reset(); reply(null, null) }
            else -> reply(null, "GoogleMobileAds has no method $method")
        }
    }

    // -- setup ------------------------------------------------------------------------------------------------

    private fun initialize(ctx: PxModuleContext, o: JSONObject, reply: PxReply) {
        configure(o)
        val app = ctx.activity.applicationContext
        // Google: initialize off the main thread, it reads disk and starts the mediation adapters.
        thread(name = "GoogleMobileAds.initialize") {
            MobileAds.initialize(app) { status ->
                reply(status.adapterStatusMap.mapValues { (_, s) ->
                    mapOf("state" to if (s.initializationState == AdapterStatus.State.READY) "ready" else "not_ready",
                        "description" to s.description, "latency" to s.latency)
                }, null)
            }
        }
    }

    private fun configure(o: JSONObject) {
        val b = MobileAds.getRequestConfiguration().toBuilder()
        if (o.has("testDeviceIds")) b.setTestDeviceIds(strings(o.optJSONArray("testDeviceIds")))
        if (o.has("tagForChildDirectedTreatment")) b.setTagForChildDirectedTreatment(when (tri(o, "tagForChildDirectedTreatment")) {
            true -> RequestConfiguration.TAG_FOR_CHILD_DIRECTED_TREATMENT_TRUE
            false -> RequestConfiguration.TAG_FOR_CHILD_DIRECTED_TREATMENT_FALSE
            null -> RequestConfiguration.TAG_FOR_CHILD_DIRECTED_TREATMENT_UNSPECIFIED
        })
        if (o.has("tagForUnderAgeOfConsent")) b.setTagForUnderAgeOfConsent(when (tri(o, "tagForUnderAgeOfConsent")) {
            true -> RequestConfiguration.TAG_FOR_UNDER_AGE_OF_CONSENT_TRUE
            false -> RequestConfiguration.TAG_FOR_UNDER_AGE_OF_CONSENT_FALSE
            null -> RequestConfiguration.TAG_FOR_UNDER_AGE_OF_CONSENT_UNSPECIFIED
        })
        if (o.has("maxAdContentRating")) b.setMaxAdContentRating(when (if (o.isNull("maxAdContentRating")) "" else o.optString("maxAdContentRating")) {
            "G" -> RequestConfiguration.MAX_AD_CONTENT_RATING_G
            "PG" -> RequestConfiguration.MAX_AD_CONTENT_RATING_PG
            "T" -> RequestConfiguration.MAX_AD_CONTENT_RATING_T
            "MA" -> RequestConfiguration.MAX_AD_CONTENT_RATING_MA
            else -> RequestConfiguration.MAX_AD_CONTENT_RATING_UNSPECIFIED
        })
        MobileAds.setRequestConfiguration(b.build())
    }

    // -- full-screen ads ----------------------------------------------------------------------------------------

    private fun event(ctx: PxModuleContext, id: Int, name: String, data: Any? = null) =
        ctx.emit("GoogleMobileAds.event", mapOf("id" to id, "event" to name, "data" to data))

    private fun load(ctx: PxModuleContext, o: JSONObject, reply: PxReply) {
        val id = o.optInt("id")
        val unit = o.optString("unitId")
        val request = Wire.request(o.optJSONObject("request"))
        val act = ctx.activity
        val ssv = o.optJSONObject("serverSideVerification")?.let {
            ServerSideVerificationOptions.Builder()
                .setUserId(if (it.isNull("userId")) "" else it.optString("userId"))
                .setCustomData(if (it.isNull("customData")) "" else it.optString("customData")).build()
        }
        val paid = { v: AdValue -> event(ctx, id, "paid", Wire.paid(v)) }
        fun keep(ad: Any) { ads[id] = ad; reply(emptyMap<String, Any?>(), null) }
        fun failed(e: LoadAdError) = reply(Wire.loadError(e), null)
        when (o.optString("type")) {
            "interstitial" -> InterstitialAd.load(act, unit, request, object : InterstitialAdLoadCallback() {
                override fun onAdLoaded(ad: InterstitialAd) { ad.setOnPaidEventListener(paid); keep(ad) }
                override fun onAdFailedToLoad(e: LoadAdError) = failed(e)
            })
            "rewarded" -> RewardedAd.load(act, unit, request, object : RewardedAdLoadCallback() {
                override fun onAdLoaded(ad: RewardedAd) {
                    ad.setOnPaidEventListener(paid); ssv?.let(ad::setServerSideVerificationOptions); keep(ad)
                }
                override fun onAdFailedToLoad(e: LoadAdError) = failed(e)
            })
            "rewarded_interstitial" -> RewardedInterstitialAd.load(act, unit, request, object : RewardedInterstitialAdLoadCallback() {
                override fun onAdLoaded(ad: RewardedInterstitialAd) {
                    ad.setOnPaidEventListener(paid); ssv?.let(ad::setServerSideVerificationOptions); keep(ad)
                }
                override fun onAdFailedToLoad(e: LoadAdError) = failed(e)
            })
            "app_open" -> AppOpenAd.load(act, unit, request, object : AppOpenAd.AppOpenAdLoadCallback() {
                override fun onAdLoaded(ad: AppOpenAd) { ad.setOnPaidEventListener(paid); keep(ad) }
                override fun onAdFailedToLoad(e: LoadAdError) = failed(e)
            })
            else -> reply(null, "unknown ad type ${o.optString("type")}")
        }
    }

    private fun show(ctx: PxModuleContext, o: JSONObject, reply: PxReply) {
        val id = o.optInt("id")
        val immersive = o.optBoolean("immersive")
        val ad = ads.remove(id) ?: run {
            reply(mapOf("shown" to false, "error" to "not_loaded", "message" to "no loaded ad with id $id"), null); return
        }
        var reward: Map<String, Any?>? = null
        var done = false
        fun finish(result: Map<String, Any?>) { if (!done) { done = true; reply(result, null) } }
        val cb = object : FullScreenContentCallback() {
            override fun onAdShowedFullScreenContent() = event(ctx, id, "opened")
            override fun onAdImpression() = event(ctx, id, "impression")
            override fun onAdClicked() = event(ctx, id, "clicked")
            override fun onAdFailedToShowFullScreenContent(e: AdError) {
                val err = Wire.showError(e)
                event(ctx, id, "show_failed", err)
                finish(err + ("shown" to false))
            }
            override fun onAdDismissedFullScreenContent() {
                event(ctx, id, "closed")
                finish(mapOf("shown" to true, "reward" to reward))
            }
        }
        val earned = OnUserEarnedRewardListener { r ->
            reward = mapOf("type" to r.type, "amount" to r.amount)
            event(ctx, id, "earned_reward", reward)
        }
        val act = ctx.activity
        try {
            when (ad) {
                is InterstitialAd -> { ad.fullScreenContentCallback = cb; ad.setImmersiveMode(immersive); ad.show(act) }
                is RewardedAd -> { ad.fullScreenContentCallback = cb; ad.setImmersiveMode(immersive); ad.show(act, earned) }
                is RewardedInterstitialAd -> { ad.fullScreenContentCallback = cb; ad.setImmersiveMode(immersive); ad.show(act, earned) }
                is AppOpenAd -> { ad.fullScreenContentCallback = cb; ad.setImmersiveMode(immersive); ad.show(act) }
            }
        } catch (e: Throwable) {
            finish(mapOf("shown" to false, "error" to "internal", "message" to (e.message ?: e.toString())))
        }
    }

    // -- consent (User Messaging Platform) ------------------------------------------------------------------------

    private fun consentRequest(ctx: PxModuleContext, o: JSONObject, reply: PxReply) {
        val act = ctx.activity
        val params = ConsentRequestParameters.Builder().setTagForUnderAgeOfConsent(o.optBoolean("tagForUnderAgeOfConsent"))
        val geo = if (o.isNull("debugGeography")) "" else o.optString("debugGeography")
        val ids = strings(o.optJSONArray("testDeviceIds"))
        if (geo.isNotEmpty() || ids.isNotEmpty()) {
            val d = ConsentDebugSettings.Builder(act)
            when (geo) {
                "eea" -> d.setDebugGeography(ConsentDebugSettings.DebugGeography.DEBUG_GEOGRAPHY_EEA)
                "regulated_us_state" -> d.setDebugGeography(ConsentDebugSettings.DebugGeography.DEBUG_GEOGRAPHY_REGULATED_US_STATE)
                "other" -> d.setDebugGeography(ConsentDebugSettings.DebugGeography.DEBUG_GEOGRAPHY_OTHER)
                "disabled" -> d.setDebugGeography(ConsentDebugSettings.DebugGeography.DEBUG_GEOGRAPHY_DISABLED)
            }
            ids.forEach { d.addTestDeviceHashedId(it) }
            params.setConsentDebugSettings(d.build())
        }
        UserMessagingPlatform.getConsentInformation(act).requestConsentInfoUpdate(act, params.build(),
            { reply(consentInfo(act), null) }, { e -> reply(formError(e), null) })
    }

    private fun consentInfo(c: Context): Map<String, Any?> {
        val i = UserMessagingPlatform.getConsentInformation(c)
        return mapOf(
            "status" to when (i.consentStatus) {
                ConsentInformation.ConsentStatus.REQUIRED -> "required"
                ConsentInformation.ConsentStatus.NOT_REQUIRED -> "not_required"
                ConsentInformation.ConsentStatus.OBTAINED -> "obtained"
                else -> "unknown"
            },
            "canRequestAds" to i.canRequestAds(),
            "privacyOptionsRequired" to
                (i.privacyOptionsRequirementStatus == ConsentInformation.PrivacyOptionsRequirementStatus.REQUIRED),
            "formAvailable" to i.isConsentFormAvailable)
    }

    private fun formError(e: FormError): Map<String, Any?> =
        mapOf("error" to "consent", "message" to e.message, "code" to e.errorCode, "domain" to "UMP")

    private fun tri(o: JSONObject, key: String): Boolean? = if (o.isNull(key)) null else o.optBoolean(key)

    private fun strings(a: JSONArray?): List<String> =
        if (a == null) emptyList() else (0 until a.length()).map { a.optString(it) }.filter { it.isNotEmpty() }
}

/** Python's wire shapes for SDK values. */
internal object Wire {
    fun request(o: JSONObject?): AdRequest {
        val b = AdRequest.Builder()
        if (o == null) return b.build()
        o.optJSONArray("keywords")?.let { a -> for (i in 0 until a.length()) b.addKeyword(a.optString(i)) }
        if (!o.isNull("contentUrl") && o.optString("contentUrl").isNotEmpty()) b.setContentUrl(o.optString("contentUrl"))
        o.optJSONArray("neighboringContentUrls")?.let { a ->
            if (a.length() > 0) b.setNeighboringContentUrls((0 until a.length()).map { a.optString(it) })
        }
        if (o.optBoolean("nonPersonalized")) {
            b.addNetworkExtrasBundle(AdMobAdapter::class.java, Bundle().apply { putString("npa", "1") })
        }
        return b.build()
    }

    fun loadError(e: LoadAdError): Map<String, Any?> = mapOf(
        "error" to when (e.code) {
            AdRequest.ERROR_CODE_NO_FILL, AdRequest.ERROR_CODE_MEDIATION_NO_FILL -> "no_fill"
            AdRequest.ERROR_CODE_NETWORK_ERROR -> "network"
            AdRequest.ERROR_CODE_INVALID_REQUEST, AdRequest.ERROR_CODE_REQUEST_ID_MISMATCH,
            AdRequest.ERROR_CODE_INVALID_AD_STRING -> "invalid_request"
            AdRequest.ERROR_CODE_APP_ID_MISSING -> "app_id_missing"
            else -> "internal"
        },
        "message" to e.message, "code" to e.code, "domain" to e.domain)

    // FullScreenContentCallback codes: 1 ad reused, 2 not ready, 3 app not in the foreground, 4 mediation.
    fun showError(e: AdError): Map<String, Any?> = mapOf(
        "error" to when (e.code) { 1 -> "already_used"; 2 -> "not_ready"; 3 -> "app_not_foreground"; else -> "internal" },
        "message" to e.message, "code" to e.code, "domain" to e.domain)

    fun paid(v: AdValue): Map<String, Any?> = mapOf(
        "valueMicros" to v.valueMicros, "currencyCode" to v.currencyCode,
        "precision" to when (v.precisionType) { 1 -> "estimated"; 2 -> "publisher_provided"; 3 -> "precise"; else -> "unknown" })
}
