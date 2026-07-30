const api = globalThis.browser || globalThis.chrome;
const buttonToggle = document.getElementById("button-toggle");
const reshowToggle = document.getElementById("reshow-shortcut-toggle");
const reshowDisplay = document.getElementById("reshow-shortcut-display");
const reshowRecordButton = document.getElementById("reshow-shortcut-record");
const reshowResetButton = document.getElementById("reshow-shortcut-reset");

const DEFAULT_RESHOW_SHORTCUT = {key: "R", code: "KeyR", shift: true, ctrl: false, alt: false, meta: false, label: "Shift+R"};

async function initButtonToggle() {
  const stored = await api.storage?.local?.get?.("rychlikButtonEnabled");
  buttonToggle.checked = stored?.rychlikButtonEnabled !== false;
  buttonToggle.addEventListener("change", () => {
    api.storage?.local?.set?.({rychlikButtonEnabled: buttonToggle.checked});
  });
}

function formatShortcut(shortcut) {
  const parts = [];
  if (shortcut.ctrl) parts.push("Ctrl");
  if (shortcut.alt) parts.push("Alt");
  if (shortcut.shift) parts.push("Shift");
  if (shortcut.meta) parts.push("Meta");
  const keyLabel = shortcut.code?.startsWith("Key") ? shortcut.code.slice(3)
    : shortcut.code?.startsWith("Digit") ? shortcut.code.slice(5)
    : shortcut.key?.toUpperCase() || "?";
  parts.push(keyLabel);
  return parts.join("+");
}

async function initReshowShortcut() {
  const stored = await api.storage?.local?.get?.(["rychlikReshowShortcutEnabled", "rychlikReshowShortcut"]);
  reshowToggle.checked = stored?.rychlikReshowShortcutEnabled !== false;
  let shortcut = stored?.rychlikReshowShortcut || DEFAULT_RESHOW_SHORTCUT;
  reshowDisplay.textContent = shortcut.label || formatShortcut(shortcut);

  reshowToggle.addEventListener("change", () => {
    api.storage?.local?.set?.({rychlikReshowShortcutEnabled: reshowToggle.checked});
  });

  reshowResetButton.addEventListener("click", () => {
    shortcut = DEFAULT_RESHOW_SHORTCUT;
    reshowDisplay.textContent = shortcut.label;
    reshowDisplay.classList.remove("recording");
    api.storage?.local?.set?.({rychlikReshowShortcut: shortcut});
  });

  reshowRecordButton.addEventListener("click", () => {
    reshowDisplay.textContent = "Stlač klávesovú skratku…";
    reshowDisplay.classList.add("recording");
    const onKeydown = event => {
      event.preventDefault();
      event.stopPropagation();
      // Modifier-only presses (just Shift/Ctrl/Alt/Meta alone) aren't a
      // usable combo — keep listening until an actual key comes with them.
      if (["Shift", "Control", "Alt", "Meta"].includes(event.key)) return;
      const recorded = {
        key: event.key, code: event.code,
        shift: event.shiftKey, ctrl: event.ctrlKey, alt: event.altKey, meta: event.metaKey,
      };
      recorded.label = formatShortcut(recorded);
      shortcut = recorded;
      reshowDisplay.textContent = shortcut.label;
      reshowDisplay.classList.remove("recording");
      api.storage?.local?.set?.({rychlikReshowShortcut: shortcut});
      document.removeEventListener("keydown", onKeydown, true);
    };
    document.addEventListener("keydown", onKeydown, true);
  });
}

initButtonToggle();
initReshowShortcut();
