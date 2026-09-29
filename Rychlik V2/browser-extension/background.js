const api = globalThis.browser || globalThis.chrome;
const mediaByTab = new Map();

function headerValue(headers, name) {
  return (headers || []).find(h => h.name.toLowerCase() === name)?.value || "";
}

api.webRequest.onHeadersReceived.addListener(details => {
  if (details.tabId < 0 || !details.url.startsWith("http")) return;
  const type = headerValue(details.responseHeaders, "content-type").toLowerCase();
  const url = details.url;
  const playlist = /mpegurl|dash\+xml/.test(type) || /\.(m3u8|mpd)(?:[?#]|$)/i.test(url);
  const media = /^(video|audio)\//.test(type) || /\.(mp4|webm)(?:[?#]|$)/i.test(url) ||
    (/octet-stream/.test(type) && /(video|media|stream|manifest|playlist|hls|dash)/i.test(url));
  // Samostatné .m4s/.ts fragmenty nie sú použiteľné bez manifestu.
  if (!playlist && (!media || /\.(m4s|ts)(?:[?#]|$)/i.test(url))) return;
  const list = mediaByTab.get(details.tabId) || [];
  list.push({url, type, playlist, time: Date.now()});
  mediaByTab.set(details.tabId, list.slice(-50));
  api.tabs.sendMessage(details.tabId, {type: "media-detected"}).catch?.(() => {});
}, {urls: ["<all_urls>"]}, ["responseHeaders"]);

api.tabs?.onRemoved?.addListener(tabId => mediaByTab.delete(tabId));

const SITE_TOGGLE_ID = "rychlik-toggle-site";

function createMenus() {
  api.contextMenus.removeAll?.();
  api.contextMenus.create({
    id: "rychlik-download",
    title: "Odoslať do Rýchlika",
    contexts: ["page", "link", "video", "audio"]
  });
  api.contextMenus.create({
    id: SITE_TOGGLE_ID,
    type: "checkbox",
    title: "Rýchlik: zobrazovať tlačidlo na tejto stránke",
    checked: true,
    contexts: ["page"]
  });
}

api.runtime.onInstalled.addListener(createMenus);
createMenus();

function hostnameOf(url) {
  try { return new URL(url || "").hostname.toLowerCase(); } catch (_) { return ""; }
}

api.contextMenus.onShown?.addListener(async (_info, tab) => {
  const hostname = hostnameOf(tab?.url);
  if (!hostname) return;
  const stored = await api.storage.local.get("rychlikDisabledSites");
  const disabledSites = Array.isArray(stored.rychlikDisabledSites) ? stored.rychlikDisabledSites : [];
  api.contextMenus.update(SITE_TOGGLE_ID, {checked: !disabledSites.includes(hostname)});
  api.contextMenus.refresh?.();
});

api.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== SITE_TOGGLE_ID) return;
  const hostname = hostnameOf(tab?.url);
  if (!hostname) return;
  const stored = await api.storage.local.get("rychlikDisabledSites");
  const disabledSites = new Set(Array.isArray(stored.rychlikDisabledSites) ? stored.rychlikDisabledSites : []);
  if (info.checked) disabledSites.delete(hostname); else disabledSites.add(hostname);
  await api.storage.local.set({rychlikDisabledSites: [...disabledSites]});
});

async function sendToRychlik(url, media = false, pageUrl = "", format = "") {
  const browserName = globalThis.browser ? "firefox" :
    (/Chromium/i.test(navigator.userAgent) ? "chromium" : "chrome");
  try {
    await rychlikBridgeFetch(api, "/download", {url, media, browser: browserName, referrer: pageUrl, format});
    api.notifications.create({
      type: "basic",
      iconUrl: "icon.svg",
      title: "Rýchlik",
      message: "Odoslané do aplikácie Rýchlik. Potvrď sťahovanie v okne aplikácie."
    });
  } catch (error) {
    const message = error.code === "no-token" || error.code === "bad-token"
      ? error.message
      : "Najprv spusti aplikáciu Rýchlik a skús to znova.";
    api.notifications.create({
      type: "basic",
      iconUrl: "icon.svg",
      title: error.code === "no-token" || error.code === "bad-token" ? "Rýchlik: token" : "Rýchlik nie je spustený",
      message
    });
  }
}

function pickDetectedMedia(tabId) {
  const list = mediaByTab.get(tabId) || [];
  return [...list].reverse().find(x => x.playlist) || [...list].reverse()[0] || null;
}

async function sendTabToRychlik(tab) {
  const pageUrl = tab?.url || "";
  if (!pageUrl.startsWith("http")) {
    api.notifications.create({
      type: "basic",
      iconUrl: "icon.svg",
      title: "Rýchlik",
      message: "Túto stránku nie je možné odoslať do Rýchlika."
    });
    return;
  }
  sendToRychlik(pageUrl, true, pageUrl);
}

api.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === "rychlik-download") {
    const target = info.srcUrl || info.linkUrl || info.pageUrl;
    const isMedia = Boolean(info.srcUrl) || !info.linkUrl ||
      /\.(mp4|webm|m3u8|mpd|mov|m4v|mp3|m4a|ogg)(?:[?#]|$)/i.test(target || "");
    if (isMedia && info.pageUrl) {
      api.tabs.query({active: true, currentWindow: true}).then(tabs => {
        const tabId = tabs[0]?.id;
        if (tabId) api.tabs.sendMessage(tabId, {type: "open-quality-panel"}).catch?.(() => {});
      });
    } else {
      sendToRychlik(target, false, info.pageUrl || "");
    }
  }
});

api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "bridge") {
    // content script -> background -> app: the token never enters the page's context
    if (!["/download", "/formats"].includes(message.path)) { sendResponse({ok: false, error: "unknown endpoint"}); return false; }
    rychlikBridgeFetch(api, message.path, message.body || {})
      .then(result => sendResponse({ok: true, result}))
      .catch(error => sendResponse({ok: false, error: error.message, code: error.code}));
    return true;
  }
  handleRuntimeMessage(message, sender);
  return false;
});

function handleRuntimeMessage(message, sender) {
  if (message?.type === "download-video") {
    const pageUrl = message.pageUrl || sender.tab?.url || "";
    let pageHost = "";
    try { pageHost = new URL(pageUrl).hostname.toLowerCase(); } catch (_) {}
    const pageExtractorPreferred = /(^|\.)(youtube\.com|youtu\.be|x\.com|twitter\.com)$/.test(pageHost);
    if (pageExtractorPreferred) {
      sendToRychlik(pageUrl, true, pageUrl);
      return;
    }
    const list = mediaByTab.get(sender.tab?.id) || [];
    // Manifest má prednosť pred priamym súborom; najnovšia odpoveď vyhráva.
    const detected = pickDetectedMedia(sender.tab?.id);
    const direct = message.directUrl && !message.directUrl.startsWith("blob:")
      ? message.directUrl : null;
    const target = detected?.url || direct;
    if (target) {
      sendToRychlik(target, true, message.pageUrl || sender.tab?.url || "");
    } else {
      if (pageUrl?.startsWith("http")) {
        sendToRychlik(pageUrl, true, pageUrl);
        api.notifications.create({
          type: "basic", iconUrl: "icon.svg", title: "Rýchlik",
          message: "Priamy stream sa nenašiel. Stránka bola odoslaná do Rýchlika na analýzu."
        });
      } else {
        api.notifications.create({
          type: "basic", iconUrl: "icon.svg", title: "Rýchlik",
          message: "Na tejto stránke sa nepodarilo nájsť video stream."
        });
      }
    }
  }
}
