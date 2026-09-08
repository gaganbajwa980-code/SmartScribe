document.addEventListener("DOMContentLoaded", async () => {
  const pageTitleEl = document.getElementById("pageTitle");
  const pageUrlEl = document.getElementById("pageUrl");
  const sendBtn = document.getElementById("sendBtn");
  const statusEl = document.getElementById("status");

  let activeTab = null;

  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs && tabs.length > 0) {
      activeTab = tabs[0];
      pageTitleEl.textContent = activeTab.title || "Untitled Tab";
      pageUrlEl.textContent = activeTab.url || "-";

      if (activeTab.url && (activeTab.url.startsWith("chrome://") || activeTab.url.startsWith("about:"))) {
        sendBtn.disabled = true;
        statusEl.className = "status error";
        statusEl.textContent = "Browser system tabs cannot be captured.";
      }
    }
  } catch (err) {
    statusEl.className = "status error";
    statusEl.textContent = "Could not inspect active tab.";
  }

  sendBtn.addEventListener("click", async () => {
    if (!activeTab || !activeTab.id) return;

    sendBtn.disabled = true;
    statusEl.className = "status info";
    statusEl.textContent = "Extracting page content...";

    try {
      // Execute DOM extractor in active tab
      const results = await chrome.scripting.executeScript({
        target: { tabId: activeTab.id },
        func: () => {
          const url = window.location.href;
          const title = document.title || "";
          const candidate = document.querySelector("article") || document.querySelector("main") || document.body;
          if (!candidate) {
            return { url, title, text: "", html: "" };
          }
          const clone = candidate.cloneNode(true);
          const denylist = [
            "input", "textarea", "select", "button", "form",
            "script", "style", "noscript", "svg", "canvas", "iframe", "object", "embed",
            "header", "footer", "nav", "aside",
            "[aria-hidden='true']", "[hidden]",
            ".ad", ".advertisement", ".banner", ".cookie-banner", "#cookie-notice"
          ];
          denylist.forEach(s => {
            try { clone.querySelectorAll(s).forEach(el => el.remove()); } catch (e) {}
          });
          const text = (clone.innerText || clone.textContent || "").trim();
          return { url, title, text, html: clone.innerHTML };
        }
      });

      if (!results || !results[0] || !results[0].result) {
        throw new Error("Failed to extract page content from active tab.");
      }

      const extracted = results[0].result;
      statusEl.textContent = "Sending to SmartScribe Desktop...";

      const resp = await fetch("http://127.0.0.1:5000/api/browser/page", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: extracted.url,
          title: extracted.title,
          html: extracted.html,
          text: extracted.text,
          source_type: "extension"
        })
      });

      const data = await resp.json();
      if (resp.ok && data.success) {
        statusEl.className = "status success";
        statusEl.textContent = `✓ Ingested! (${data.word_count || 0} words)`;
      } else {
        statusEl.className = "status error";
        statusEl.textContent = data.message || "Failed to process page.";
      }
    } catch (err) {
      statusEl.className = "status error";
      if (err.message && err.message.includes("Failed to fetch")) {
        statusEl.textContent = "Error: SmartScribe is not running on 127.0.0.1:5000.";
      } else {
        statusEl.textContent = `Error: ${err.message}`;
      }
    } finally {
      sendBtn.disabled = false;
    }
  });
});
