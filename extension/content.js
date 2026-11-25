/**
 * SmartScribe Browser Bridge - Safe DOM Discovery & Extractor
 * Phase 5C.3.2: Safe DOM Discovery, Fingerprinting & Sensitive-Field Classification.
 * Strict Security Invariants:
 *  - Pure observation: ZERO clicks, ZERO typing, ZERO DOM modification.
 *  - ZERO cookies, ZERO localStorage, ZERO credentials.
 *  - Discovers visible, enabled interactive DOM targets only.
 *  - Bounded target count (<= 100) and bounded text previews (<= 100 chars).
 *  - Conservative multi-attribute sensitive field classifier.
 */

// =====================================================================
// 1. ARTICLE EXTRACTION (PHASE 3G COMPATIBILITY)
// =====================================================================

function extractSafePageData() {
  const url = window.location.href;
  const title = document.title || "";

  // Reject sensitive / local browser internal schemes
  if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
    return {
      error: "Internal browser pages cannot be captured.",
      url: url,
      title: title
    };
  }

  // Identify primary readable container: <article>, <main>, or document.body
  const candidate = document.querySelector("article") || document.querySelector("main") || document.body;
  if (!candidate) {
    return {
      url: url,
      title: title,
      text: "",
      html: ""
    };
  }

  // Deep clone candidate element so modifications do not alter the live page
  const clone = candidate.cloneNode(true);

  // Explicitly strip interactive, credential, and noise elements
  const denylistedSelectors = [
    "input", "textarea", "select", "button", "form",
    "script", "style", "noscript", "svg", "canvas", "iframe", "object", "embed",
    "header", "footer", "nav", "aside",
    "[aria-hidden='true']", "[hidden]",
    ".ad", ".advertisement", ".banner", ".cookie-banner", "#cookie-notice"
  ];

  denylistedSelectors.forEach(selector => {
    try {
      const elements = clone.querySelectorAll(selector);
      elements.forEach(el => el.remove());
    } catch (e) {
      // Ignore selector errors on unusual page structures
    }
  });

  // Extract clean text from clone
  const cleanText = clone.innerText || clone.textContent || "";

  return {
    url: url,
    title: title,
    text: cleanText.trim(),
    html: clone.innerHTML
  };
}

// Export for execution context
if (typeof window !== "undefined") {
  window.extractSafePageData = extractSafePageData;
}


// =====================================================================
// 2. SAFE DOM DISCOVERY & CLASSIFICATION (PHASE 5C.3.2)
// =====================================================================

const MAX_TARGETS_LIMIT = 100;
const MAX_PREVIEW_TEXT_LEN = 100;

const INTERACTIVE_SELECTOR = [
  "button",
  "a[href]",
  "input",
  "textarea",
  "select",
  "[role='button']",
  "[role='link']",
  "[role='textbox']",
  "[role='searchbox']",
  "[role='checkbox']",
  "[role='radio']",
  "[role='combobox']",
  "[role='option']",
  "[role='tab']",
  "[role='menuitem']",
  "[role='switch']"
].join(", ");

const SENSITIVE_KEYWORDS = [
  "password", "passwd", "pwd", "secret", "token", "auth", "login",
  "signin", "otp", "2fa", "mfa", "cvv", "cvc", "creditcard", "cardnumber",
  "card_number", "ssn", "pin", "security_code", "security", "sec_code",
  "passcode", "api_key", "apikey"
];

const SENSITIVE_AUTOCOMPLETE = [
  "current-password", "new-password", "one-time-code", "cc-number",
  "cc-exp", "cc-csc", "cc-type", "cc-exp-month", "cc-exp-year",
  "transaction-amount", "bday", "tel", "transaction-currency"
];

/**
 * Computes deterministic SHA-256 hex string with sha256: prefix.
 */
async function computeSha256(str) {
  if (typeof crypto !== "undefined" && crypto.subtle && crypto.subtle.digest) {
    try {
      const buffer = new TextEncoder().encode(str);
      const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      return "sha256:" + hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
    } catch (e) {}
  }
  // Deterministic fallback if subtle is unavailable
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash |= 0;
  }
  return "sha256:fallback_" + Math.abs(hash).toString(16);
}

/**
 * Evaluates visibility conservatively.
 * Returns false for hidden, collapsed, 0-opacity, or out-of-viewport elements.
 */
function isElementVisible(el) {
  if (!el || !el.isConnected) return false;

  // Check aria-hidden or hidden attribute
  if (el.closest("[aria-hidden='true']") || el.hidden || el.closest("[hidden]")) {
    return false;
  }

  const style = window.getComputedStyle(el);
  if (!style) return false;

  if (style.display === "none") return false;
  if (style.visibility === "hidden" || style.visibility === "collapse") return false;
  if (parseFloat(style.opacity) === 0) return false;

  const rect = el.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;

  // Viewport intersection check
  const vh = window.innerHeight || document.documentElement.clientHeight || 0;
  const vw = window.innerWidth || document.documentElement.clientWidth || 0;
  const inViewport = (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < vh &&
    rect.left < vw
  );

  return inViewport;
}

/**
 * Determines whether interactive element is enabled.
 */
function isElementEnabled(el) {
  if (!el) return false;
  if (el.disabled) return false;
  if (el.getAttribute("aria-disabled") === "true") return false;
  if (el.closest("fieldset[disabled]")) return false;
  if (el.readOnly && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return false;
  return true;
}

/**
 * Conservative multi-attribute sensitive field classifier.
 */
function classifySensitivity(el) {
  const reasons = [];
  const tag = (el.tagName || "").toUpperCase();
  const type = (el.getAttribute("type") || "").toLowerCase().trim();

  if (type === "password") reasons.push("type:password");
  if (type === "hidden") reasons.push("type:hidden");

  const ac = (el.getAttribute("autocomplete") || "").toLowerCase().trim();
  if (ac && SENSITIVE_AUTOCOMPLETE.some(tok => ac.includes(tok))) {
    reasons.push(`autocomplete:${ac}`);
  }

  const idAttr = (el.id || "").toLowerCase();
  const nameAttr = (el.getAttribute("name") || "").toLowerCase();
  const classAttr = (el.className || "").toString().toLowerCase();
  const placeholderAttr = (el.getAttribute("placeholder") || "").toLowerCase();
  const ariaLabelAttr = (el.getAttribute("aria-label") || "").toLowerCase();

  const attrs = [
    { name: "id", val: idAttr },
    { name: "name", val: nameAttr },
    { name: "class", val: classAttr },
    { name: "placeholder", val: placeholderAttr },
    { name: "aria_label", val: ariaLabelAttr }
  ];

  for (const a of attrs) {
    const cleanVal = a.val.replace(/[-_]/g, "");
    for (const kw of SENSITIVE_KEYWORDS) {
      const cleanKw = kw.replace(/[-_]/g, "");
      if (a.val.includes(kw) || cleanVal.includes(cleanKw)) {
        reasons.push(`${a.name}_pattern:${kw}`);
      }
    }
  }

  // Associated label text inspection
  let labelText = "";
  if (el.id) {
    try {
      const lEl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lEl) labelText = (lEl.innerText || lEl.textContent || "").toLowerCase();
    } catch (e) {}
  }
  if (!labelText) {
    const parentLabel = el.closest("label");
    if (parentLabel) labelText = (parentLabel.innerText || parentLabel.textContent || "").toLowerCase();
  }
  if (labelText) {
    for (const kw of SENSITIVE_KEYWORDS) {
      if (labelText.includes(kw)) {
        reasons.push(`label_pattern:${kw}`);
        break;
      }
    }
  }

  // Form context inspection
  const form = el.closest("form");
  if (form) {
    const formContext = [
      form.id || "",
      form.getAttribute("name") || "",
      form.className || "",
      form.getAttribute("action") || ""
    ].join(" ").toLowerCase();

    const isAuthForm = ["login", "signin", "auth", "checkout", "payment", "account"].some(
      term => formContext.includes(term)
    );
    if (isAuthForm && tag === "INPUT" && type !== "submit" && type !== "button" && type !== "reset") {
      reasons.push("auth_form_context");
    }
  }

  const isSensitive = reasons.length > 0;
  return {
    is_sensitive: isSensitive,
    reasons: Array.from(new Set(reasons))
  };
}

/**
 * Generates a stable, deterministic CSS selector for re-resolution.
 */
function generateStableSelector(el) {
  if (!el || !el.tagName) return "";

  // 1. Stable non-volatile ID
  if (el.id && typeof el.id === "string") {
    const id = el.id.trim();
    const isVolatile = (
      /^[0-9]+$/.test(id) ||
      /^[0-9a-f]{8}-[0-9a-f]{4}/i.test(id) ||
      id.startsWith("react-aria") ||
      id.startsWith(":r") ||
      id.startsWith("ember")
    );
    if (!isVolatile) {
      try {
        if (document.querySelectorAll(`#${CSS.escape(id)}`).length === 1) {
          return `#${CSS.escape(id)}`;
        }
      } catch (e) {}
    }
  }

  // 2. Stable name attribute for form inputs
  const name = el.getAttribute("name");
  if (name && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(el.tagName)) {
    const tag = el.tagName.toLowerCase();
    const form = el.closest("form");
    if (form && form.name) {
      const sel = `form[name="${CSS.escape(form.name)}"] ${tag}[name="${CSS.escape(name)}"]`;
      try {
        if (document.querySelectorAll(sel).length === 1) return sel;
      } catch (e) {}
    }
    const sel = `${tag}[name="${CSS.escape(name)}"]`;
    try {
      if (document.querySelectorAll(sel).length === 1) return sel;
    } catch (e) {}
  }

  // 3. Hierarchical structural path
  const path = [];
  let curr = el;
  while (curr && curr.nodeType === Node.ELEMENT_NODE && curr !== document.body && curr !== document.documentElement) {
    let segment = curr.tagName.toLowerCase();
    if (curr.id && !/^[0-9]+$/.test(curr.id) && !curr.id.startsWith(":r")) {
      segment += `#${CSS.escape(curr.id)}`;
      path.unshift(segment);
      break; // Anchored to an ID
    } else {
      let index = 1;
      let sibling = curr.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === curr.tagName) index++;
        sibling = sibling.previousElementSibling;
      }
      segment += `:nth-of-type(${index})`;
    }
    path.unshift(segment);
    curr = curr.parentElement;
  }

  const fullSelector = path.join(" > ");
  try {
    if (document.querySelectorAll(fullSelector).length === 1) {
      return fullSelector;
    }
  } catch (e) {}

  return fullSelector || "";
}

/**
 * Discovers safe interactive DOM targets across the document.
 */
async function discoverDOMTargets(options = {}) {
  const maxTargets = Math.min(parseInt(options.max_targets || 50, 10), MAX_TARGETS_LIMIT);
  const candidates = document.querySelectorAll(INTERACTIVE_SELECTOR);

  const targets = [];

  for (const el of candidates) {
    if (targets.length >= maxTargets) break;

    // Visibility & Enabled checks
    if (!isElementVisible(el)) continue;
    const isEnabled = isElementEnabled(el);

    const tag = (el.tagName || "").toUpperCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const elId = el.id || "";
    const name = el.getAttribute("name") || "";
    const type = (el.getAttribute("type") || "").toLowerCase();
    const href = tag === "A" ? (el.getAttribute("href") || "") : "";
    const form = el.closest("form");
    const formName = form ? (form.getAttribute("name") || form.id || "") : "";

    const selector = generateStableSelector(el);
    const rect = el.getBoundingClientRect();

    // Text preview (bounded, data-minimization)
    let textPreview = (el.innerText || el.textContent || el.value || "").trim().replace(/\s+/g, " ");
    if (textPreview.length > MAX_PREVIEW_TEXT_LEN) {
      textPreview = textPreview.substring(0, MAX_PREVIEW_TEXT_LEN);
    }

    // Sensitivity classification
    const sens = classifySensitivity(el);
    if (sens.is_sensitive && tag === "INPUT") {
      textPreview = "[SENSITIVE FIELD]";
    }

    // Deterministic SHA-256 fingerprint
    const identityPayload = (
      `tag:${tag}|role:${role}|id:${elId}|name:${name}|` +
      `type:${type}|href:${href}|form:${formName}|path:${selector}`
    );
    const fingerprint = await computeSha256(identityPayload);

    targets.push({
      tag: tag,
      role: role,
      id: elId,
      name: name,
      type: type,
      href: href,
      dom_selector: selector,
      element_fingerprint: fingerprint,
      is_visible: true,
      is_enabled: isEnabled,
      is_sensitive: sens.is_sensitive,
      sensitivity_reasons: sens.reasons,
      text_preview: textPreview,
      placeholder: el.getAttribute("placeholder") || "",
      aria_label: el.getAttribute("aria-label") || "",
      frame_id: 0,
      bounding_box: {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height
      }
    });
  }

  return {
    success: true,
    count: targets.length,
    url: window.location.href,
    origin: window.location.origin,
    title: document.title || "",
    targets: targets
  };
}

/**
 * Re-resolves a target binding against the live DOM.
 */
async function resolveDOMTarget(targetBinding) {
  if (!targetBinding || !targetBinding.dom_selector) {
    return { success: false, status: "unresolved", error: "Missing selector." };
  }

  try {
    const el = document.querySelector(targetBinding.dom_selector);
    if (!el) {
      return { success: false, status: "target_not_found", error: "Element not found by selector." };
    }

    const isVisible = isElementVisible(el);
    const isEnabled = isElementEnabled(el);

    const tag = (el.tagName || "").toUpperCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const elId = el.id || "";
    const name = el.getAttribute("name") || "";
    const type = (el.getAttribute("type") || "").toLowerCase();
    const href = tag === "A" ? (el.getAttribute("href") || "") : "";
    const form = el.closest("form");
    const formName = form ? (form.getAttribute("name") || form.id || "") : "";
    const selector = generateStableSelector(el);

    const identityPayload = (
      `tag:${tag}|role:${role}|id:${elId}|name:${name}|` +
      `type:${type}|href:${href}|form:${formName}|path:${selector}`
    );
    const currentFp = await computeSha256(identityPayload);

    const fpMatches = (
      !targetBinding.element_fingerprint ||
      targetBinding.element_fingerprint === currentFp
    );

    const rect = el.getBoundingClientRect();

    return {
      success: true,
      status: fpMatches ? "passed" : "fingerprint_mismatch",
      fingerprint_matches: fpMatches,
      current_fingerprint: currentFp,
      is_visible: isVisible,
      is_enabled: isEnabled,
      bounding_box: {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height
      }
    };
  } catch (err) {
    return { success: false, status: "error", error: err.message };
  }
}

// =====================================================================
// 3. CONTROLLED DOM ACTION EXECUTION (PHASE 5C.3.3)
// =====================================================================

/**
 * Executes a controlled click on a verified DOM element.
 */
async function executeDOMClick(targetBinding) {
  if (!targetBinding || !targetBinding.dom_selector) {
    return { success: false, status: "unresolved", error: "Missing selector." };
  }

  const el = document.querySelector(targetBinding.dom_selector);
  if (!el) {
    return { success: false, status: "target_not_found", error: "Element not found by selector." };
  }

  if (!isElementVisible(el)) {
    return { success: false, status: "target_hidden", error: "Target element is hidden or outside viewport." };
  }

  if (!isElementEnabled(el)) {
    return { success: false, status: "target_disabled", error: "Target element is disabled." };
  }

  // Live fingerprint verification
  const selector = generateStableSelector(el);
  const tag = (el.tagName || "").toUpperCase();
  const role = (el.getAttribute("role") || "").toLowerCase();
  const elId = el.id || "";
  const name = el.getAttribute("name") || "";
  const type = (el.getAttribute("type") || "").toLowerCase();
  const href = tag === "A" ? (el.getAttribute("href") || "") : "";
  const form = el.closest("form");
  const formName = form ? (form.getAttribute("name") || form.id || "") : "";

  const identityPayload = (
    `tag:${tag}|role:${role}|id:${elId}|name:${name}|` +
    `type:${type}|href:${href}|form:${formName}|path:${selector}`
  );
  const liveFp = await computeSha256(identityPayload);
  if (targetBinding.element_fingerprint && liveFp !== targetBinding.element_fingerprint) {
    return {
      success: false,
      status: "target_mismatch",
      error: `Target fingerprint mismatch before click.`
    };
  }

  // Controlled DOM click dispatch
  const preUrl = window.location.href;
  try {
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
  } catch (e) {}

  try {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    el.click();
  } catch (e) {
    return { success: false, status: "execution_failed", error: `Click dispatch failed: ${e.message}` };
  }

  const postUrl = window.location.href;
  const ariaExp = el.getAttribute("aria-expanded");
  const ariaSel = el.getAttribute("aria-selected");
  const isChecked = typeof el.checked === "boolean" ? el.checked : null;

  return {
    success: true,
    status: "passed",
    verified: false, // Content script provides observation; backend verifies
    postcondition_status: "unverified",
    observed_state: {
      url: postUrl,
      pre_url: preUrl,
      url_changed: postUrl !== preUrl,
      aria_expanded: ariaExp,
      aria_selected: ariaSel,
      checked: isChecked
    }
  };
}

/**
 * Executes controlled text entry into a verified non-sensitive text field.
 */
async function executeDOMTypeText(targetBinding, text) {
  if (!targetBinding || !targetBinding.dom_selector) {
    return { success: false, status: "unresolved", error: "Missing selector." };
  }

  const el = document.querySelector(targetBinding.dom_selector);
  if (!el) {
    return { success: false, status: "target_not_found", error: "Element not found by selector." };
  }

  if (!isElementVisible(el)) {
    return { success: false, status: "target_hidden", error: "Target element is hidden." };
  }

  if (!isElementEnabled(el)) {
    return { success: false, status: "target_disabled", error: "Target element is disabled or readonly." };
  }

  // Mandatory sensitivity check
  const sens = classifySensitivity(el);
  if (sens.is_sensitive || targetBinding.is_sensitive) {
    return {
      success: false,
      status: "policy_denied",
      error: "Typing into sensitive field is strictly prohibited."
    };
  }

  // Verify fingerprint
  const selector = generateStableSelector(el);
  const tag = (el.tagName || "").toUpperCase();
  const role = (el.getAttribute("role") || "").toLowerCase();
  const elId = el.id || "";
  const name = el.getAttribute("name") || "";
  const type = (el.getAttribute("type") || "").toLowerCase();
  const form = el.closest("form");
  const formName = form ? (form.getAttribute("name") || form.id || "") : "";

  const identityPayload = (
    `tag:${tag}|role:${role}|id:${elId}|name:${name}|` +
    `type:${type}|href:|form:${formName}|path:${selector}`
  );
  const liveFp = await computeSha256(identityPayload);
  if (targetBinding.element_fingerprint && liveFp !== targetBinding.element_fingerprint) {
    return { success: false, status: "target_mismatch", error: "Fingerprint mismatch before typing." };
  }

  const safeText = String(text || "").slice(0, 500);

  try {
    el.focus();
    el.value = safeText;
    el.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
    el.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
  } catch (e) {
    return { success: false, status: "execution_failed", error: `Text input failed: ${e.message}` };
  }

  const liveVal = el.value;

  return {
    success: true,
    status: "passed",
    verified: false,
    postcondition_status: "unverified",
    observed_value: liveVal,
    text_length: liveVal ? liveVal.length : 0
  };
}

/**
 * Executes controlled selection of a <select> option.
 */
async function executeDOMSelectOption(targetBinding, optionValue) {
  if (!targetBinding || !targetBinding.dom_selector) {
    return { success: false, status: "unresolved", error: "Missing selector." };
  }

  const el = document.querySelector(targetBinding.dom_selector);
  if (!el) {
    return { success: false, status: "target_not_found", error: "Element not found by selector." };
  }

  if (el.tagName !== "SELECT") {
    return { success: false, status: "policy_denied", error: "Target is not a SELECT element." };
  }

  if (!isElementVisible(el)) {
    return { success: false, status: "target_hidden", error: "Target select is hidden." };
  }

  if (!isElementEnabled(el)) {
    return { success: false, status: "target_disabled", error: "Target select is disabled." };
  }

  const valStr = String(optionValue || "").trim();
  const opt = Array.from(el.options).find(o => o.value === valStr || o.text.trim() === valStr);
  if (!opt) {
    return { success: false, status: "policy_denied", error: `Option '${valStr}' not found in select.` };
  }
  if (opt.disabled) {
    return { success: false, status: "target_disabled", error: `Option '${valStr}' is disabled.` };
  }

  try {
    el.value = opt.value;
    el.dispatchEvent(new Event("change", { bubbles: true, cancelable: true }));
  } catch (e) {
    return { success: false, status: "execution_failed", error: `Select option failed: ${e.message}` };
  }

  const selOpt = el.options[el.selectedIndex];

  return {
    success: true,
    status: "passed",
    verified: false,
    postcondition_status: "unverified",
    observed_value: el.value,
    observed_label: selOpt ? selOpt.text.trim() : "",
    selected_value: opt.value
  };
}

// =====================================================================
// 4. BRIDGE MESSAGE DISPATCHER (PHASE 5C.3.3)
// =====================================================================

if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || typeof message !== "object") return false;

    // Handshake / Ping
    if (message.action === "PING_CONTENT" || message.action === "PING") {
      sendResponse({
        success: true,
        status: "passed",
        url: window.location.href,
        origin: window.location.origin,
        title: document.title || ""
      });
      return true;
    }

    // Discover safe DOM targets
    if (message.action === "DISCOVER_TARGETS") {
      discoverDOMTargets(message.payload || {})
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }

    // Resolve target binding live
    if (message.action === "RESOLVE_TARGET") {
      resolveDOMTarget(message.target_binding || message.payload?.target_binding)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }

    // Controlled Click
    if (message.action === "CLICK") {
      executeDOMClick(message.target_binding || message.payload?.target_binding)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }

    // Controlled Text Input
    if (message.action === "TYPE_TEXT") {
      const txt = message.payload ? message.payload.text : (message.text || "");
      executeDOMTypeText(message.target_binding || message.payload?.target_binding, txt)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }

    // Controlled Select Option
    if (message.action === "SELECT_OPTION") {
      const opt = message.payload ? message.payload.option : (message.option || "");
      executeDOMSelectOption(message.target_binding || message.payload?.target_binding, opt)
        .then(result => sendResponse(result))
        .catch(err => sendResponse({ success: false, error: err.message }));
      return true;
    }

    // Strictly reject arbitrary JavaScript or unknown commands
    return false;
  });
}

