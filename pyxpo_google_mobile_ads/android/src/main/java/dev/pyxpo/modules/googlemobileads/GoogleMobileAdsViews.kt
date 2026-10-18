package dev.pyxpo.modules.googlemobileads

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.google.android.gms.ads.AdListener
import com.google.android.gms.ads.AdLoader
import com.google.android.gms.ads.AdSize
import com.google.android.gms.ads.AdView
import com.google.android.gms.ads.LoadAdError
import com.google.android.gms.ads.nativead.MediaView
import com.google.android.gms.ads.nativead.NativeAd
import com.google.android.gms.ads.nativead.NativeAdOptions
import com.google.android.gms.ads.nativead.NativeAdView
import dev.pyxpo.moduleapi.PxNativeView
import org.json.JSONObject

private typealias Emit = (name: String, value: Any?) -> Unit

/** Lays its one child out at the child's own measured size, so an ad keeps its size while Python's box catches up. */
private class AdBox(activity: Activity, val onWidth: (Int) -> Unit) : FrameLayout(activity) {
    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (w != oldw && w > 0) post { onWidth(w) }
    }
}

private fun JSONObject.str(key: String): String = if (isNull(key)) "" else optString(key)

/**
 * `PxBannerAd`: an AdView. Adaptive sizes wait for the box's width, then pick Google's adaptive size for it;
 * the height goes to Python (`size`) once an ad loads, so a banner with no fill stays 0 high.
 * The unit, size or request changing (or an adaptive banner's width) makes a new AdView, as the SDK sets the unit once.
 */
internal class BannerAdView(private val activity: Activity, private val emit: Emit) : PxNativeView {
    private val box = AdBox(activity) { relayout() }
    override val view: View get() = box
    private var props = JSONObject()
    private var ad: AdView? = null
    private var builtFor = ""

    private val density get() = activity.resources.displayMetrics.density

    override fun update(props: JSONObject, removed: Set<String>) {
        val keys = props.keys()
        while (keys.hasNext()) { val k = keys.next(); this.props.put(k, props.get(k)) }
        removed.forEach { this.props.remove(it) }
        relayout()
    }

    private fun adSize(widthDp: Int): AdSize? {
        val size = props.str("size")
        return when (size) {
            "ANCHORED_ADAPTIVE_BANNER" -> if (widthDp > 0) AdSize.getCurrentOrientationAnchoredAdaptiveBannerAdSize(activity, widthDp) else null
            "INLINE_ADAPTIVE_BANNER" -> if (widthDp <= 0) null else if (props.isNull("max_height"))
                AdSize.getCurrentOrientationInlineAdaptiveBannerAdSize(activity, widthDp)
                else AdSize.getInlineAdaptiveBannerAdSize(widthDp, props.optInt("max_height"))
            "BANNER" -> AdSize.BANNER
            "LARGE_BANNER" -> AdSize.LARGE_BANNER
            "MEDIUM_RECTANGLE" -> AdSize.MEDIUM_RECTANGLE
            "FULL_BANNER" -> AdSize.FULL_BANNER
            "LEADERBOARD" -> AdSize.LEADERBOARD
            else -> Regex("^(\\d+)x(\\d+)$").find(size)?.let { AdSize(it.groupValues[1].toInt(), it.groupValues[2].toInt()) }
        }
    }

    private fun relayout() {
        val unit = props.str("unit_id")
        if (unit.isEmpty()) return
        val adaptive = props.str("size").endsWith("_ADAPTIVE_BANNER")
        val widthDp = (box.width / density).toInt()
        val key = listOf(unit, props.str("size"), props.opt("max_height"), props.opt("request"),
            if (adaptive) widthDp else 0).toString()
        if (key == builtFor) return
        val size = adSize(widthDp) ?: return
        builtFor = key
        ad?.let { box.removeView(it); it.destroy() }
        val v = AdView(activity)
        v.adUnitId = unit
        v.setAdSize(size)
        v.adListener = object : AdListener() {
            override fun onAdLoaded() {
                val s = v.adSize ?: size
                val dims = mapOf("width" to s.width, "height" to s.height)
                emit("size", dims)
                emit("load", dims)
            }
            override fun onAdFailedToLoad(e: LoadAdError) = emit("error", Wire.loadError(e))
            override fun onAdOpened() = emit("open", null)
            override fun onAdClosed() = emit("close", null)
            override fun onAdClicked() = emit("click", null)
            override fun onAdImpression() = emit("impression", null)
        }
        v.setOnPaidEventListener { emit("paid", Wire.paid(it)) }
        box.addView(v, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.CENTER_HORIZONTAL))
        ad = v
        v.loadAd(Wire.request(props.optJSONObject("request")))
    }

    override fun dispose() {
        ad?.destroy()
        ad = null
        box.removeAllViews()
    }
}

/**
 * `PxNativeAd`: Google's small or medium template, built in code around a NativeAdView (the SDK adds the
 * AdChoices icon itself). Once an ad loads, the template is measured at the box's width and its height goes
 * to Python (`size`).
 */
internal class NativeAdBox(private val activity: Activity, private val emit: Emit) : PxNativeView {
    private val box = AdBox(activity) { w -> if (current != null) measure(w) }
    override val view: View get() = box
    private var props = JSONObject()
    private var current: NativeAd? = null
    private var shown: NativeAdView? = null
    private var builtFor = ""
    private var drawnLook = ""
    private var gone = false

    private fun dp(v: Int) = (v * activity.resources.displayMetrics.density).toInt()

    override fun update(props: JSONObject, removed: Set<String>) {
        val keys = props.keys()
        while (keys.hasNext()) { val k = keys.next(); this.props.put(k, props.get(k)) }
        removed.forEach { this.props.remove(it) }
        val unit = this.props.str("unit_id")
        val key = listOf(unit, this.props.opt("request")).toString()
        if (unit.isNotEmpty() && key != builtFor) {
            builtFor = key
            load(unit, key)
        } else if (look() != drawnLook) current?.let(::render)  // a color or template change
    }

    // The props the template is drawn from. Other updates (Python re-renders with new callbacks) keep the view: a
    // fresh NativeAdView re-registers the ad.
    private fun look() = listOf("template", "background_color", "text_color", "button_color", "button_text_color")
        .joinToString("|") { props.opt(it)?.toString() ?: "" }

    private fun load(unit: String, key: String) {
        AdLoader.Builder(activity, unit)
            .forNativeAd { ad ->
                if (gone || key != builtFor) { ad.destroy(); return@forNativeAd }
                current?.destroy()
                current = ad
                ad.setOnPaidEventListener { emit("paid", Wire.paid(it)) }
                render(ad)
                emit("load", null)
            }
            .withAdListener(object : AdListener() {
                override fun onAdFailedToLoad(e: LoadAdError) { if (key == builtFor) emit("error", Wire.loadError(e)) }
                override fun onAdClicked() = emit("click", null)
                override fun onAdImpression() = emit("impression", null)
                override fun onAdOpened() = emit("open", null)
                override fun onAdClosed() = emit("close", null)
            })
            .withNativeAdOptions(NativeAdOptions.Builder().build())
            .build()
            .loadAd(Wire.request(props.optJSONObject("request")))
    }

    private fun color(key: String, default: Int): Int = if (props.isNull(key)) default else props.optLong(key).toInt()

    private fun render(ad: NativeAd) {
        drawnLook = look()
        val medium = props.str("template") != "small"
        val text = color("text_color", Color.rgb(0x20, 0x20, 0x20))
        val buttonBg = color("button_color", Color.rgb(0x1A, 0x73, 0xE8))
        val buttonText = color("button_text_color", Color.WHITE)
        val nav = NativeAdView(activity)
        nav.setBackgroundColor(color("background_color", Color.WHITE))
        val col = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(10), dp(8), dp(10), dp(10)) }

        val badge = TextView(activity).apply {
            this.text = "Ad"; textSize = 11f; setTypeface(typeface, Typeface.BOLD); setTextColor(Color.WHITE)
            setPadding(dp(4), 0, dp(4), 0)
            background = GradientDrawable().apply { setColor(Color.rgb(0xF9, 0xAB, 0x00)); cornerRadius = dp(3).toFloat() }
        }
        val icon = ImageView(activity).apply { scaleType = ImageView.ScaleType.CENTER_CROP }
        ad.icon?.drawable?.let { icon.setImageDrawable(it) } ?: run { icon.visibility = View.GONE }
        val headline = TextView(activity).apply {
            this.text = ad.headline; textSize = 15f; setTypeface(typeface, Typeface.BOLD); setTextColor(text)
            maxLines = if (medium) 2 else 1; ellipsize = TextUtils.TruncateAt.END
        }
        val sub = TextView(activity).apply {
            this.text = if (medium) (ad.advertiser ?: "") else (ad.body ?: "")
            textSize = 12f; setTextColor(text); alpha = 0.7f; maxLines = 1; ellipsize = TextUtils.TruncateAt.END
            if (this.text.isNullOrEmpty()) visibility = View.GONE
        }
        val cta = Button(activity).apply {
            this.text = ad.callToAction ?: ""; isAllCaps = false; textSize = 14f; setTextColor(buttonText)
            background = GradientDrawable().apply { setColor(buttonBg); cornerRadius = dp(8).toFloat() }
            minHeight = 0; minimumHeight = 0; setPadding(dp(14), dp(8), dp(14), dp(8))
            if (ad.callToAction.isNullOrEmpty()) visibility = View.GONE
        }

        val texts = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            addView(LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                addView(badge)
                addView(headline, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(6) })
            })
            addView(sub)
        }
        val top = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            val s = dp(if (medium) 40 else 48)
            addView(icon, LinearLayout.LayoutParams(s, s).apply { marginEnd = dp(10) })
            addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            if (!medium) addView(cta, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) })
        }
        col.addView(top)
        nav.iconView = icon
        nav.headlineView = headline
        nav.callToActionView = cta
        if (medium) {
            // AdMob: the media view is at least 120x120dp.
            val media = MediaView(activity)
            col.addView(media, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(180)).apply { topMargin = dp(8) })
            nav.mediaView = media
            val body = TextView(activity).apply {
                this.text = ad.body ?: ""; textSize = 13f; setTextColor(text); maxLines = 2; ellipsize = TextUtils.TruncateAt.END
                if (ad.body.isNullOrEmpty()) visibility = View.GONE
            }
            col.addView(body, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) })
            nav.bodyView = body
            if (!ad.advertiser.isNullOrEmpty()) nav.advertiserView = sub
            col.addView(cta, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) })
        } else if (!ad.body.isNullOrEmpty()) {
            nav.bodyView = sub
        }
        nav.addView(col)
        nav.setNativeAd(ad)

        shown?.let { box.removeView(it) }
        shown = nav
        box.addView(nav, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        if (box.width > 0) measure(box.width)
    }

    /** The template's height at this width, to Python in dp. */
    private fun measure(widthPx: Int) {
        val nav = shown ?: return
        nav.measure(View.MeasureSpec.makeMeasureSpec(widthPx, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
        val d = activity.resources.displayMetrics.density
        emit("size", mapOf("width" to Math.round(widthPx / d), "height" to Math.round(nav.measuredHeight / d)))
    }

    override fun dispose() {
        gone = true
        shown?.destroy()
        current?.destroy()
        current = null
        shown = null
        box.removeAllViews()
    }
}
