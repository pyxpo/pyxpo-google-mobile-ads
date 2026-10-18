// pyxpo-google-mobile-ads in the browser and the desktop window: simulated test ads, so ad flows can be tried in development.
// Python only sends Google's test unit IDs here (see `_sim_only` in __init__.py). Nothing is fetched from the network.
// The page loads this file after app.js (see pyxpoPage in app.js).
(function () {
  "use strict";

  const LOAD_MS = 300;                       // a simulated load still takes a moment, like a real one
  const COUNTDOWN = { interstitial: 3, rewarded: 5, rewarded_interstitial: 5, app_open: 0 };
  const REWARD = { type: "coins", amount: 10 };
  const FIXED = { BANNER: [320, 50], LARGE_BANNER: [320, 100], MEDIUM_RECTANGLE: [300, 250], FULL_BANNER: [468, 60],
    LEADERBOARD: [728, 90] };
  const NATIVE_HEIGHT = { small: 96, medium: 330 };

  const ads = new Map();                     // id -> ad type, between load and show
  const TYPES = ["interstitial", "rewarded", "rewarded_interstitial", "app_open"];

  // 0xAARRGGBB (Python's parse_color) -> a CSS color.
  function css(c, fallback) {
    if (typeof c !== "number" || !isFinite(c)) return fallback;
    c = c >>> 0;
    return "rgba(" + ((c >>> 16) & 255) + "," + ((c >>> 8) & 255) + "," + (c & 255) + "," + (((c >>> 24) & 255) / 255) + ")";
  }

  // The banner size for a box this wide, in CSS px (dp). Adaptive heights follow Google's rule of thumb: about 15% of
  // the width, 50 to 90 high; an inline banner may be taller, up to max_height.
  function bannerSize(size, width, maxHeight) {
    if (FIXED[size]) return FIXED[size];
    const m = /^(\d+)x(\d+)$/.exec(size || "");
    if (m) return [+m[1], +m[2]];
    if (!(width > 0)) return null;
    const w = Math.round(width);
    if (size === "INLINE_ADAPTIVE_BANNER") {
      const h = Math.round(w * 0.25);
      return [w, Math.max(50, maxHeight ? Math.min(maxHeight, h) : h)];
    }
    return [w, Math.max(50, Math.min(90, Math.round(w * 0.15)))];
  }

  function badge() {
    const b = document.createElement("span");
    b.textContent = "Ad";
    b.style.cssText = "background:#f9ab00;color:#fff;font:bold 11px sans-serif;padding:1px 4px;border-radius:3px;" +
      "margin-right:6px;flex:none";
    return b;
  }

  function watchWidth(el, cb) {
    let last = -1;
    const check = () => {
      const w = el.offsetWidth || 0;
      if (w !== last) { last = w; cb(w); }
    };
    let ro = null;
    if (typeof ResizeObserver !== "undefined") { ro = new ResizeObserver(check); ro.observe(el); }
    if (typeof requestAnimationFrame !== "undefined") requestAnimationFrame(check); else setTimeout(check, 0);
    return () => { if (ro) ro.disconnect(); };
  }

  function clickable(el, emit) {
    el.style.cursor = "pointer";
    el.addEventListener("click", () => {
      emit("click", null);
      emit("open", null);
      setTimeout(() => emit("close", null), 0);   // a real ad opens the browser; the test ad comes straight back
    });
  }

  function makeBanner(el, emit) {
    let props = {}, built = "", timer = null, width = 0;
    el.style.overflow = "hidden";
    const card = document.createElement("div");
    card.style.cssText = "box-sizing:border-box;margin:0 auto;height:100%;display:none;align-items:center;" +
      "justify-content:center;background:#f1f3f4;border:1px dashed #9aa0a6;color:#3c4043;font:13px sans-serif";
    el.appendChild(card);
    clickable(card, emit);

    function relayout() {
      const s = bannerSize(props.size, width, props.max_height);
      if (!props.unit_id || !s) return;
      const key = [props.unit_id, props.size, props.max_height, JSON.stringify(props.request || {}), s[0], s[1]].join("|");
      if (key === built) return;
      built = key;
      clearTimeout(timer);
      timer = setTimeout(() => {
        card.style.display = "flex";
        card.style.width = s[0] + "px";
        card.textContent = "";
        card.appendChild(badge());
        card.appendChild(document.createTextNode("Test ad " + s[0] + "x" + s[1]));
        const d = { width: s[0], height: s[1] };
        emit("size", d);
        emit("load", d);
        emit("impression", null);
      }, LOAD_MS);
    }
    const stop = watchWidth(el, (w) => { width = w; relayout(); });
    return {
      update(p, removed) {
        Object.assign(props, p);
        for (const k of removed || []) delete props[k];
        relayout();
      },
      dispose() { clearTimeout(timer); stop(); },
    };
  }

  function makeNative(el, emit) {
    let props = {}, built = "", timer = null;
    el.style.overflow = "hidden";
    const card = document.createElement("div");
    el.appendChild(card);
    clickable(card, emit);

    function render() {
      const medium = props.template !== "small";
      const text = css(props.text_color, "#202124");
      const btn = "background:" + css(props.button_color, "#1a73e8") + ";color:" + css(props.button_text_color, "#fff") +
        ";border-radius:8px;padding:8px 14px;font:600 14px sans-serif;text-align:center;flex:none";
      card.style.cssText = "box-sizing:border-box;height:" + NATIVE_HEIGHT[medium ? "medium" : "small"] + "px;" +
        "padding:8px 10px;display:flex;flex-direction:column;gap:8px;font-family:sans-serif;color:" + text +
        ";background:" + css(props.background_color, "#fff");
      card.innerHTML = "";
      const top = document.createElement("div");
      top.style.cssText = "display:flex;align-items:center;gap:10px";
      const icon = document.createElement("div");
      const side = medium ? 40 : 48;
      icon.style.cssText = "width:" + side + "px;height:" + side + "px;border-radius:6px;background:#fbbc04;flex:none";
      const texts = document.createElement("div");
      texts.style.cssText = "flex:1;min-width:0";
      const head = document.createElement("div");
      head.style.cssText = "display:flex;align-items:center;font:bold 15px sans-serif";
      head.appendChild(badge());
      head.appendChild(document.createTextNode("Test native ad"));
      const sub = document.createElement("div");
      sub.style.cssText = "font-size:12px;opacity:0.7";
      sub.textContent = medium ? "pyxpo" : "A simulated ad for development.";
      texts.appendChild(head);
      texts.appendChild(sub);
      top.appendChild(icon);
      top.appendChild(texts);
      const cta = document.createElement("div");
      cta.style.cssText = btn;
      cta.textContent = "Install";
      card.appendChild(top);
      if (medium) {
        const media = document.createElement("div");
        media.style.cssText = "height:180px;border-radius:6px;background:linear-gradient(135deg,#4285f4,#34a853)";
        const body = document.createElement("div");
        body.style.cssText = "font-size:13px";
        body.textContent = "A simulated ad for development.";
        card.appendChild(media);
        card.appendChild(body);
        card.appendChild(cta);
      } else {
        top.appendChild(cta);
      }
    }

    function reload() {
      if (!props.unit_id) return;
      const key = props.unit_id + "|" + JSON.stringify(props.request || {});
      if (key === built) { if (card.firstChild) render(); return; }   // a color or template change
      built = key;
      clearTimeout(timer);
      timer = setTimeout(() => {
        render();
        const h = NATIVE_HEIGHT[props.template === "small" ? "small" : "medium"];
        emit("size", { width: el.offsetWidth || 0, height: h });
        emit("load", null);
        emit("impression", null);
      }, LOAD_MS);
    }
    return {
      update(p, removed) {
        Object.assign(props, p);
        for (const k of removed || []) delete props[k];
        reload();
      },
      dispose() { clearTimeout(timer); },
    };
  }

  // A full-screen test ad: a dark overlay with a countdown; rewarded types earn the reward when it ends.
  function show(o, page) {
    const type = ads.get(o.id);
    if (!type) return Promise.resolve({ shown: false, error: "not_loaded", message: "no loaded ad with id " + o.id });
    ads.delete(o.id);
    const ev = (event, data) => { if (page.emit) page.emit("GoogleMobileAds.event", { id: o.id, event, data: data == null ? null : data }); };
    const rewarded = type === "rewarded" || type === "rewarded_interstitial";
    return new Promise((resolve) => {
      let left = COUNTDOWN[type] || 0, reward = null, tick = null;
      const root = document.createElement("div");
      root.setAttribute("data-pyxpo-ad", type);
      root.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:rgba(32,33,36,0.96);color:#fff;" +
        "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;font-family:sans-serif";
      const title = document.createElement("div");
      title.style.cssText = "display:flex;align-items:center;font:bold 22px sans-serif";
      title.appendChild(badge());
      title.appendChild(document.createTextNode("Test " + type.replace("_", " ") + " ad"));
      const note = document.createElement("div");
      note.style.cssText = "font-size:14px;opacity:0.8";
      const close = document.createElement("button");
      close.setAttribute("aria-label", "Close ad");
      close.style.cssText = "position:absolute;top:16px;right:16px;font:16px sans-serif;padding:6px 12px;border-radius:16px;" +
        "border:0;background:#fff;color:#202124;cursor:pointer";
      root.appendChild(title);
      root.appendChild(note);
      root.appendChild(close);
      const paint = () => {
        close.disabled = left > 0;
        close.textContent = left > 0 ? String(left) : "Close";
        note.textContent = rewarded ? (reward ? "Reward earned: " + REWARD.amount + " " + REWARD.type
          : "Watch to earn " + REWARD.amount + " " + REWARD.type) : "A simulated ad for development.";
      };
      const finish = () => {
        clearInterval(tick);
        root.remove();
        ev("closed");
        resolve({ shown: true, reward });
      };
      title.addEventListener("click", () => ev("clicked"));
      close.addEventListener("click", () => { if (left <= 0) finish(); });
      const done = () => {
        if (rewarded && !reward) { reward = { type: REWARD.type, amount: REWARD.amount }; ev("earned_reward", reward); }
        paint();
      };
      paint();
      document.body.appendChild(root);
      ev("opened");
      ev("impression");
      if (left <= 0) done();
      else tick = setInterval(() => { left -= 1; if (left <= 0) { clearInterval(tick); done(); } else paint(); }, 1000);
    });
  }

  pyxpoPage.defineView("PxBannerAd", makeBanner);
  pyxpoPage.defineView("PxNativeAd", makeNative);
  pyxpoPage.defineModule("GoogleMobileAds", (method, args, page) => {
    const o = args[0] || {};
    switch (method) {
      case "load":
        if (TYPES.indexOf(o.type) < 0) throw new Error("unknown ad type " + o.type);
        return new Promise((resolve) => setTimeout(() => { ads.set(o.id, o.type); resolve({}); }, LOAD_MS));
      case "show": return show(o, page);
      case "destroy": ads.delete(o.id); return null;
      case "initialize": return { simulated: { state: "ready", description: "pyxpo simulated test ads", latency: 0 } };
      case "setRequestConfiguration": case "consentReset": return null;
      case "trackingStatus": case "requestTracking": return "authorized";
      case "openAdInspector": return {};
      case "consentRequestInfo": case "consentShowFormIfRequired": case "consentShowPrivacyOptionsForm":
        return { status: "not_required", canRequestAds: true, privacyOptionsRequired: false, formAvailable: false };
    }
    throw new Error("GoogleMobileAds has no method " + method);
  });
  makeBanner.bannerSize = bannerSize;   // for tests
  makeBanner.css = css;
})();
