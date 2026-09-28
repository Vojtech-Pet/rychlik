(() => {
  if (globalThis.__rychlikVideoDetector) return;
  globalThis.__rychlikVideoDetector = true;
  const api = globalThis.browser || globalThis.chrome;
  let button;
  let panel;
  let networkMediaDetected = false;
  let lastAnchor = {top: 76, right: 18};
  let customPosition = loadCustomPosition();
  let suppressNextClick = false;
  const mediaUrls = new Set();
  const qualityCache = new Map();
  const qualityRequests = new Map();
  const CACHE_TTL_MS = 10 * 60 * 1000;
  const hostname = location.hostname.toLowerCase();
  const topLevel = window.top === window;
  let globallyDisabled = false;
  let disabledSites = [];
  let dismissedThisLoad = false;
  let reshowShortcutEnabled = true;
  let reshowShortcut = {key: "r", code: "KeyR", shift: true, ctrl: false, alt: false, meta: false, label: "Shift+R"};

  function buttonAllowed() {
    return !globallyDisabled && !disabledSites.includes(hostname) && !dismissedThisLoad;
  }

  function hideForThisSite() {
    if (!disabledSites.includes(hostname)) disabledSites = [...disabledSites, hostname];
    api.storage?.local?.set?.({rychlikDisabledSites: disabledSites}).catch(() => {});
    closePanel();
    button?.remove();
    button = undefined;
  }

  // × dismisses the button for the current page load only (in-memory, no
  // storage write); the configurable keyboard shortcut (default Shift+R)
  // toggles it back on and off again without a reload. hideForThisSite()
  // above is the permanent per-hostname version, used by the right-click
  // context menu entry instead.
  function dismissForThisLoad() {
    dismissedThisLoad = true;
    closePanel();
    button?.remove();
    button = undefined;
  }

  function addCloseButton(target) {
    const closeButton = document.createElement("span");
    closeButton.className = "rychlik-close";
    closeButton.title = `Skryť (${reshowShortcut.label || "klávesová skratka"} ho prepne späť)`;
    closeButton.textContent = "×";
    closeButton.addEventListener("pointerdown", event => event.stopPropagation());
    closeButton.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      dismissForThisLoad();
    });
    target.appendChild(closeButton);
  }

  function matchesShortcut(event, shortcut) {
    return event.code === shortcut.code
      && event.shiftKey === Boolean(shortcut.shift)
      && event.ctrlKey === Boolean(shortcut.ctrl)
      && event.altKey === Boolean(shortcut.alt)
      && event.metaKey === Boolean(shortcut.meta);
  }

  document.addEventListener("keydown", event => {
    if (!reshowShortcutEnabled) return;
    if (!matchesShortcut(event, reshowShortcut)) return;
    const tag = (event.target?.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || event.target?.isContentEditable) return;
    event.preventDefault();
    // The shortcut toggles the same global "show floating button" setting as
    // the popup checkbox — not just this page's dismissedThisLoad flag —
    // otherwise it can't turn the button back on once it's off globally.
    globallyDisabled = !globallyDisabled;
    if (!globallyDisabled) dismissedThisLoad = false;
    api.storage?.local?.set?.({rychlikButtonEnabled: !globallyDisabled}).catch(() => {});
    update();
  }, true);

  function loadToggleSettings() {
    return Promise.resolve(api.storage?.local?.get?.(
      ["rychlikButtonEnabled", "rychlikDisabledSites", "rychlikReshowShortcutEnabled", "rychlikReshowShortcut"]))
      .then(stored => {
        globallyDisabled = stored?.rychlikButtonEnabled === false;
        disabledSites = Array.isArray(stored?.rychlikDisabledSites) ? stored.rychlikDisabledSites : [];
        reshowShortcutEnabled = stored?.rychlikReshowShortcutEnabled !== false;
        if (stored?.rychlikReshowShortcut) reshowShortcut = stored.rychlikReshowShortcut;
      })
      .catch(() => {});
  }

  api.storage?.onChanged?.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes.rychlikButtonEnabled || changes.rychlikDisabledSites) {
      loadToggleSettings().then(update);
    } else if (changes.rychlikReshowShortcutEnabled || changes.rychlikReshowShortcut) {
      loadToggleSettings();
    }
  });

  function isLikelyVideoPage() {
    const host = location.hostname.toLowerCase();
    if ((host === "youtube.com" || host.endsWith(".youtube.com")) && location.pathname === "/watch") return true;
    if (host === "youtu.be" || host.endsWith(".youtu.be")) return true;
    return /(video|watch|embed|player|tube|film|movie|clip|shorts)/i.test(location.href);
  }

  function styleButton() {
    Object.assign(button.style, {
      all: "initial",
      position: "fixed",
      zIndex: "2147483647",
      top: `${lastAnchor.top}px`,
      right: `${lastAnchor.right}px`,
      display: "block",
      visibility: "visible",
      opacity: "1",
      minWidth: "122px",
      padding: "4px 7px",
      border: "1px solid #c8d1df",
      borderRadius: "6px",
      background: "#ffffff",
      color: "#172033",
      boxShadow: "0 4px 14px rgba(15,23,42,.25)",
      font: "700 11px system-ui, sans-serif",
      cursor: "pointer"
    });
  }

  function positionStorageKey() {
    return `rychlik-button-position:${location.hostname}`;
  }

  function loadCustomPosition() {
    try {
      const saved = JSON.parse(localStorage.getItem(`rychlik-button-position:${location.hostname}`) || "null");
      if (saved && Number.isFinite(saved.top) && Number.isFinite(saved.right)) return saved;
    } catch (_) {}
    return null;
  }

  function saveCustomPosition() {
    try {
      localStorage.setItem(positionStorageKey(), JSON.stringify(customPosition));
    } catch (_) {}
  }

  function bestVideoElement() {
    const videos = [...document.querySelectorAll("video")];
    return videos
      .map(video => ({video, box: video.getBoundingClientRect()}))
      .filter(item => item.box.width > 160 && item.box.height > 90)
      .sort((a, b) => (b.box.width * b.box.height) - (a.box.width * a.box.height))[0]?.video || null;
  }

  function updateAnchor() {
    const video = bestVideoElement();
    if (customPosition) {
      lastAnchor = {
        top: Math.max(8, Math.min(window.innerHeight - 30, customPosition.top)),
        right: Math.max(8, Math.min(window.innerWidth - 120, customPosition.right)),
      };
    } else if (video) {
      const box = video.getBoundingClientRect();
      lastAnchor = {
        top: Math.max(8, Math.round(box.top + 8)),
        right: Math.max(8, Math.round(window.innerWidth - box.right + 8)),
      };
    } else {
      lastAnchor = {top: 76, right: 18};
    }
    if (button) {
      button.style.top = `${lastAnchor.top}px`;
      button.style.right = `${lastAnchor.right}px`;
    }
    if (panel) {
      panel.style.top = `${lastAnchor.top + 25}px`;
      panel.style.right = `${lastAnchor.right}px`;
    }
  }

  function makeButtonDraggable() {
    let dragging = false;
    let moved = false;
    let startX = 0;
    let startY = 0;
    let startTop = 0;
    let startRight = 0;

    button.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      dragging = true;
      moved = false;
      startX = event.clientX;
      startY = event.clientY;
      startTop = lastAnchor.top;
      startRight = lastAnchor.right;
      button.setPointerCapture?.(event.pointerId);
    });

    button.addEventListener("pointermove", event => {
      if (!dragging) return;
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      if (Math.abs(dx) + Math.abs(dy) > 5) moved = true;
      customPosition = {
        top: Math.max(8, Math.min(window.innerHeight - 30, startTop + dy)),
        right: Math.max(8, Math.min(window.innerWidth - 120, startRight - dx)),
      };
      updateAnchor();
    });

    button.addEventListener("pointerup", event => {
      if (!dragging) return;
      dragging = false;
      button.releasePointerCapture?.(event.pointerId);
      if (moved) {
        suppressNextClick = true;
        saveCustomPosition();
        event.preventDefault();
        event.stopPropagation();
      }
      setTimeout(() => { moved = false; }, 0);
    });

    button.addEventListener("click", event => {
      if (suppressNextClick) {
        suppressNextClick = false;
        event.preventDefault();
        event.stopPropagation();
      }
    }, true);
  }

  function remember(url) {
    if (url && !url.startsWith("blob:") && /\.(mp4|webm|m3u8|mpd)(?:[?#]|$)/i.test(url)) {
      mediaUrls.add(url);
      if (mediaUrls.size > 30) mediaUrls.delete(mediaUrls.values().next().value);
    }
  }

  async function post(path, body) {
    const api = globalThis.browser || globalThis.chrome;
    const reply = await api.runtime.sendMessage({type: "bridge", path, body});
    if (!reply || !reply.ok) {
      const error = new Error(reply?.error || "Aplikácia Rýchlik neodpovedá.");
      error.code = reply?.code;
      throw error;
    }
    return reply.result;
  }

  function cacheKey() {
    return location.href.split("#", 1)[0];
  }

  async function getFormats() {
    const key = cacheKey();
    const cached = qualityCache.get(key);
    if (cached && Date.now() - cached.time < CACHE_TTL_MS) {
      return cached.formats;
    }
    if (qualityRequests.has(key)) {
      return qualityRequests.get(key);
    }
    const request = post("/formats", {
      url: location.href,
      media: true,
      browser: "firefox",
      referrer: location.href
    }).then(result => {
      const formats = result.formats || [];
      qualityCache.set(key, {time: Date.now(), formats});
      qualityRequests.delete(key);
      return formats;
    }).catch(error => {
      qualityRequests.delete(key);
      throw error;
    });
    qualityRequests.set(key, request);
    return request;
  }

  function prefetchFormats() {
    if (!qualityCache.has(cacheKey()) && !qualityRequests.has(cacheKey())) {
      getFormats().catch(() => {});
    }
  }

  function findEmbeddedMediaUrl() {
    const attributeCandidate = [...document.querySelectorAll("[src], [href]")]
      .map(element => element.src || element.href || "")
      .find(value => /^https?:.*\.(?:m3u8|mpd|mp4|webm)(?:[?#]|$)/i.test(value));
    if (attributeCandidate) return attributeCandidate.replaceAll("&amp;", "&");

    for (const script of document.scripts) {
      const text = (script.textContent || "").replaceAll("\\/", "/");
      const preferred = text.match(/(?:hlsAuto|manifest|playlist|dash)["']?\s*[:=]\s*["'](https?:[^"'<>\\\s]+)/i);
      const found = preferred || text.match(/https?:[^"'<>\\\s]+(?:\.m3u8|\.mpd|\.mp4|\.webm)(?:\?[^"'<>\\\s]*)?/i);
      if (found) return (preferred ? found[1] : found[0]).replaceAll("&amp;", "&");
    }
    return "";
  }

  function closePanel() {
    panel?.remove();
    panel = undefined;
  }

  function setButtonReady() {
    if (!button) return;
    button.textContent = "";
    const play = document.createElement("span");
    play.className = "rychlik-play";
    play.textContent = "↓";
    const label = document.createElement("span");
    label.textContent = "Rýchlik video";
    const caret = document.createElement("span");
    caret.className = "rychlik-caret";
    caret.textContent = "▾";
    button.append(play, label, caret);
    addCloseButton(button);
  }

  function setPanelTitle(text) {
    panel.textContent = "";
    const title = document.createElement("div");
    title.className = "rychlik-quality-title";
    title.textContent = text;
    panel.appendChild(title);
    return title;
  }

  function toggleQualityPanel() {
    if (panel) {
      closePanel();
      return;
    }
    showQualityPanel();
  }

  async function sendQuality(format) {
    await post("/download", {
      url: location.href,
      media: true,
      browser: "firefox",
      referrer: location.href,
      format
    });
    closePanel();
    button.textContent = "✓ Odoslané";
    setTimeout(() => {
      setButtonReady();
    }, 2200);
  }

  async function showQualityPanel() {
    closePanel();
    panel = document.createElement("div");
    panel.id = "rychlik-quality-panel";
    updateAnchor();
    panel.style.top = `${lastAnchor.top + 25}px`;
    panel.style.right = `${lastAnchor.right}px`;
    setPanelTitle("Rýchlik video");
    const status = document.createElement("div");
    status.className = "rychlik-quality-status";
    status.textContent = "Načítavam kvality…";
    panel.appendChild(status);
    document.body.appendChild(panel);
    try {
      const formats = await getFormats();
      setPanelTitle("Vyber kvalitu");
      formats.forEach((item, index) => {
        const quality = document.createElement("button");
        quality.className = "rychlik-quality-option";
        quality.textContent = item.label === "Najlepšia kvalita"
          ? `${index + 1}. Najlepšia kvalita`
          : `${index + 1}. Video ${item.label}`;
        quality.addEventListener("click", () => sendQuality(item.format).catch(error => {
          panel.querySelector(".rychlik-quality-title").textContent = "Chyba";
          panel.append(String(error.message || error));
        }));
        panel.appendChild(quality);
      });
      const close = document.createElement("button");
      close.className = "rychlik-quality-close";
      close.textContent = "Zavrieť";
      close.addEventListener("click", closePanel);
      panel.appendChild(close);
    } catch (error) {
      setPanelTitle(error.premium ? "Platený obsah" : "Kvality sa nepodarilo načítať");
      const message = document.createElement("div");
      message.className = "rychlik-quality-error";
      message.textContent = String(error.message || error);
      panel.appendChild(message);
      if (!error.premium) {
        const best = document.createElement("button");
        best.className = "rychlik-quality-option";
        best.textContent = "Stiahnuť najlepšiu kvalitu";
        best.addEventListener("click", () => sendQuality("bestvideo+bestaudio/best"));
        panel.appendChild(best);
      }
      const close = document.createElement("button");
      close.className = "rychlik-quality-close";
      close.textContent = "Zavrieť";
      close.addEventListener("click", closePanel);
      panel.appendChild(close);
    }
  }

  try {
    for (const entry of performance.getEntriesByType("resource")) remember(entry.name);
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) remember(entry.name);
    }).observe({type: "resource", buffered: true});
  } catch (_) {}

  function update() {
    if (!topLevel) return;
    if (!location.href.startsWith("http")) return;
    const videos = [...document.querySelectorAll("video")];
    const embeddedPlayer = document.querySelector('iframe[src*="/embed/"], iframe[allowfullscreen]');
    const visibleVideo = videos.some(video => {
      const box = video.getBoundingClientRect();
      return box.width > 160 && box.height > 90;
    });
    const hasPlayerMarkup = Boolean(embeddedPlayer || document.querySelector('source[type*="video"], source[type*="mpegURL" i]'));
    const visible = visibleVideo || networkMediaDetected || hasPlayerMarkup || isLikelyVideoPage();
    const allowed = buttonAllowed();
    if (visible && allowed) updateAnchor();
    if (visible && allowed && !button) {
      button = document.createElement("button");
      button.id = "rychlik-video-button";
      button.title = "Stiahnuť video cez Rýchlik";
      setButtonReady();
      styleButton();
      makeButtonDraggable();
      prefetchFormats();
      button.addEventListener("click", () => {
        const video = videos.find(v => !v.paused && v.readyState > 0) || videos.find(v => v.readyState > 0);
        let direct = video?.currentSrc && !video.currentSrc.startsWith("blob:")
          ? video.currentSrc : [...mediaUrls].at(-1);
        if (!direct && embeddedPlayer?.src) direct = embeddedPlayer.src;
        if (!direct) {
          direct = findEmbeddedMediaUrl();
        }
        toggleQualityPanel();
      });
      (document.body || document.documentElement).appendChild(button);
    } else if ((!visible || !allowed) && button) {
      button.remove(); button = undefined;
      closePanel();
    }
  }

  new MutationObserver(update).observe(document.documentElement, {childList: true, subtree: true});
  api.runtime.onMessage.addListener(message => {
    if (message?.type === "media-detected") {
      networkMediaDetected = true;
      update();
    } else if (message?.type === "open-quality-panel") {
      toggleQualityPanel();
    }
  });
  setInterval(update, 2000);
  loadToggleSettings().then(update);
})();
