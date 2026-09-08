/**
 * SmartScribe Browser Bridge - Safe Read-Only DOM Extractor
 * Strictly user-initiated.
 * Never accesses cookies, localStorage, credentials, passwords, or form inputs.
 */

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
