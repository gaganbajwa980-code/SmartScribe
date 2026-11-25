/**
 * SmartScribe Browser Bridge - Manifest V3 Background Service Worker
 * Phase 5C.3.1: Foundational Bridge Protocol & Transport
 * 
 * Strict Security Invariants:
 *  - Only connects to loopback (http://127.0.0.1:5000).
 *  - Single-session secret token authentication (X-SmartScribe-Token).
 *  - Standardized request/response correlation with request_id.
 *  - verified is NEVER set to true for execution/dispatch.
 *  - Zero DOM modification, zero clicking, zero typing in Phase 5C.3.1.
 *  - Zero cookies, zero credentials, zero clipboard, zero CDP/debugger.
 */

const SERVER_BASE = "http://127.0.0.1:5000";
const TOKEN_HEADER = "X-SmartScribe-Token";

let sessionToken = null;
let isPolling = false;
let pollBackoffMs = 1000;

/**
 * Initializes or fetches session token from the local SmartScribe loopback server.
 */
async function getSessionToken() {
  if (sessionToken) {
    return sessionToken;
  }
  try {
    const resp = await fetch(`${SERVER_BASE}/api/browser/bridge/token`, {
      method: "GET",
      headers: { "Accept": "application/json" }
    });
    if (resp.ok) {
      const data = await resp.json();
      if (data.success && data.token) {
        sessionToken = data.token;
        return sessionToken;
      }
    }
  } catch (err) {
    // Server might not be running yet
  }
  return null;
}

/**
 * Sends heartbeat check-in to server.
 */
async function sendHeartbeat() {
  const token = await getSessionToken();
  if (!token) return false;

  try {
    const resp = await fetch(`${SERVER_BASE}/api/browser/bridge/heartbeat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [TOKEN_HEADER]: token
      },
      body: JSON.stringify({
        source: "manifest_v3_background",
        timestamp: Date.now() / 1000.0,
        version: "1.0.0"
      })
    });
    return resp.ok;
  } catch (err) {
    return false;
  }
}

/**
 * Handles incoming bridge commands.
 * Phase 5C.3.1 strictly limits actions to non-modifying protocol queries:
 * PING, GET_ACTIVE_TAB, GET_PAGE_METADATA.
 */
async function handleBridgeRequest(req) {
  const reqId = req.request_id || "unknown";
  const action = req.action || "UNKNOWN";
  const startTime = Date.now();

  try {
    if (action === "PING") {
      return {
        request_id: reqId,
        success: true,
        status: "passed",
        verified: false, // Invariant: execution != verification
        postcondition_status: "unverified",
        execution_latency_ms: Date.now() - startTime,
        verification_latency_ms: 0.0,
        payload: { pong: true, time: Date.now() / 1000.0 }
      };
    }

    if (action === "GET_ACTIVE_TAB") {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tabs || tabs.length === 0) {
        return {
          request_id: reqId,
          success: false,
          status: "target_not_found",
          verified: false,
          postcondition_status: "unverified",
          error: "No active browser tab found.",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }

      const activeTab = tabs[0];
      const url = activeTab.url || "";
      if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
        return {
          request_id: reqId,
          success: false,
          status: "policy_denied",
          verified: false,
          postcondition_status: "unverified",
          error: "Internal browser pages are restricted.",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }

      return {
        request_id: reqId,
        success: true,
        status: "passed",
        verified: false,
        postcondition_status: "unverified",
        execution_latency_ms: Date.now() - startTime,
        verification_latency_ms: 0.0,
        payload: {
          tab_id: activeTab.id,
          window_id: activeTab.windowId,
          url: activeTab.url,
          title: activeTab.title || "",
          fav_icon_url: activeTab.favIconUrl || ""
        }
      };
    }

    // Forward to content script if tab_id is specified
    if (action === "PING_CONTENT" && req.tab_id) {
      try {
        const contentResp = await chrome.tabs.sendMessage(req.tab_id, req);
        return {
          request_id: reqId,
          success: Boolean(contentResp && contentResp.success),
          status: contentResp ? (contentResp.status || "passed") : "error",
          verified: false,
          postcondition_status: "unverified",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0,
          payload: contentResp || {}
        };
      } catch (err) {
        return {
          request_id: reqId,
          success: false,
          status: "error",
          verified: false,
          postcondition_status: "unverified",
          error: `Content script messaging failed: ${err.message}`,
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }
    }

    // Phase 5C.3.2: Safe DOM Discovery
    if (action === "DISCOVER_TARGETS") {
      let targetTabId = req.tab_id;
      if (!targetTabId) {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tabs || tabs.length === 0) {
          return {
            request_id: reqId,
            success: false,
            status: "target_not_found",
            verified: false,
            postcondition_status: "unverified",
            error: "No active browser tab found.",
            execution_latency_ms: Date.now() - startTime,
            verification_latency_ms: 0.0
          };
        }
        targetTabId = tabs[0].id;
      }

      const tab = await chrome.tabs.get(targetTabId);
      const url = tab.url || "";
      if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
        return {
          request_id: reqId,
          success: false,
          status: "policy_denied",
          verified: false,
          postcondition_status: "unverified",
          error: "Internal browser pages are restricted.",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }

      try {
        const contentResp = await chrome.tabs.sendMessage(targetTabId, {
          action: "DISCOVER_TARGETS",
          payload: req.payload || {}
        });
        let pathName = "/";
        try {
          if (contentResp && contentResp.url) {
            pathName = new URL(contentResp.url).pathname;
          }
        } catch (e) {}

        return {
          request_id: reqId,
          success: Boolean(contentResp && contentResp.success),
          status: contentResp && contentResp.success ? "passed" : "error",
          verified: false,
          postcondition_status: "unverified",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0,
          payload: {
            tab_id: tab.id,
            window_id: tab.windowId,
            origin: contentResp?.origin || "",
            url_path: pathName,
            title: contentResp?.title || tab.title || "",
            targets: contentResp?.targets || [],
            count: contentResp?.count || 0
          }
        };
      } catch (err) {
        return {
          request_id: reqId,
          success: false,
          status: "error",
          verified: false,
          postcondition_status: "unverified",
          error: `Content script discovery messaging failed: ${err.message}`,
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }
    }

    // Phase 5C.3.2: Re-resolve Target
    if (action === "RESOLVE_TARGET") {
      let targetTabId = req.tab_id;
      if (!targetTabId) {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tabs && tabs.length > 0) targetTabId = tabs[0].id;
      }
      if (!targetTabId) {
        return {
          request_id: reqId,
          success: false,
          status: "target_not_found",
          verified: false,
          postcondition_status: "unverified",
          error: "No target tab available.",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }

      try {
        const contentResp = await chrome.tabs.sendMessage(targetTabId, {
          action: "RESOLVE_TARGET",
          target_binding: req.target_binding || req.payload?.target_binding
        });
        return {
          request_id: reqId,
          success: Boolean(contentResp && contentResp.success),
          status: contentResp ? (contentResp.status || "passed") : "error",
          verified: false,
          postcondition_status: "unverified",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0,
          payload: contentResp || {}
        };
      } catch (err) {
        return {
          request_id: reqId,
          success: false,
          status: "error",
          verified: false,
          postcondition_status: "unverified",
          error: `Target resolution failed: ${err.message}`,
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }
    // Phase 5C.3.3: Controlled Actions (CLICK, TYPE_TEXT, SELECT_OPTION)
    if (action === "CLICK" || action === "TYPE_TEXT" || action === "SELECT_OPTION") {
      let targetTabId = req.tab_id;
      if (!targetTabId) {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tabs && tabs.length > 0) targetTabId = tabs[0].id;
      }
      if (!targetTabId) {
        return {
          request_id: reqId,
          success: false,
          status: "target_not_found",
          verified: false,
          postcondition_status: "unverified",
          error: "No target tab available.",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }

      try {
        const tab = await chrome.tabs.get(targetTabId);
        const url = tab.url || "";
        if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
          return {
            request_id: reqId,
            success: false,
            status: "policy_denied",
            verified: false,
            postcondition_status: "unverified",
            error: "Interaction on internal browser pages is prohibited.",
            execution_latency_ms: Date.now() - startTime,
            verification_latency_ms: 0.0
          };
        }
      } catch (e) {
        return {
          request_id: reqId,
          success: false,
          status: "browser_unavailable",
          verified: false,
          postcondition_status: "unverified",
          error: `Target tab ${targetTabId} inaccessible: ${e.message}`,
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }

      try {
        const contentResp = await chrome.tabs.sendMessage(targetTabId, {
          action: action,
          target_binding: req.target_binding || req.payload?.target_binding,
          payload: req.payload || {}
        });
        return {
          request_id: reqId,
          success: Boolean(contentResp && contentResp.success),
          status: contentResp ? (contentResp.status || "passed") : "error",
          verified: false,
          postcondition_status: "unverified",
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0,
          payload: contentResp || {},
          error: contentResp && !contentResp.success ? contentResp.error : null
        };
      } catch (err) {
        return {
          request_id: reqId,
          success: false,
          status: "error",
          verified: false,
          postcondition_status: "unverified",
          error: `Content action dispatch failed: ${err.message}`,
          execution_latency_ms: Date.now() - startTime,
          verification_latency_ms: 0.0
        };
      }
    }

    // Unknown or unsupported action
    return {
      request_id: reqId,
      success: false,
      status: "policy_denied",
      verified: false,
      postcondition_status: "unverified",
      error: `Action '${action}' is not authorized or supported.`,
      execution_latency_ms: Date.now() - startTime,
      verification_latency_ms: 0.0
    };
  } catch (err) {
    return {
      request_id: reqId,
      success: false,
      status: "error",
      verified: false,
      postcondition_status: "unverified",
      error: err.message || "Internal extension bridge error.",
      execution_latency_ms: Date.now() - startTime,
      verification_latency_ms: 0.0
    };
  }
}

/**
 * Sends correlated response back to server.
 */
async function sendResponse(responseObj) {
  const token = await getSessionToken();
  if (!token) return;

  try {
    await fetch(`${SERVER_BASE}/api/browser/bridge/response`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [TOKEN_HEADER]: token
      },
      body: JSON.stringify(responseObj)
    });
  } catch (err) {
    // Network failure delivering response
  }
}

/**
 * Main polling worker loop.
 */
async function pollLoop() {
  if (isPolling) return;
  isPolling = true;

  while (true) {
    try {
      const token = await getSessionToken();
      if (!token) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        continue;
      }

      // Poll server with 3-second long-poll
      const resp = await fetch(`${SERVER_BASE}/api/browser/bridge/poll?timeout=3.0`, {
        method: "GET",
        headers: {
          "Accept": "application/json",
          [TOKEN_HEADER]: token
        }
      });

      if (resp.ok) {
        pollBackoffMs = 1000;
        const data = await resp.json();
        if (data.success && data.has_request && data.request) {
          const result = await handleBridgeRequest(data.request);
          await sendResponse(result);
        }
      } else if (resp.status === 401) {
        // Token invalidated or changed; reset and re-obtain
        sessionToken = null;
        await new Promise(resolve => setTimeout(resolve, 2000));
      } else {
        await new Promise(resolve => setTimeout(resolve, pollBackoffMs));
        pollBackoffMs = Math.min(pollBackoffMs * 1.5, 10000);
      }
    } catch (err) {
      // Server down or offline; backoff
      await new Promise(resolve => setTimeout(resolve, pollBackoffMs));
      pollBackoffMs = Math.min(pollBackoffMs * 1.5, 10000);
    }
  }
}

// Lifecycle listeners
chrome.runtime.onInstalled.addListener(() => {
  sendHeartbeat();
  pollLoop();
});

chrome.runtime.onStartup.addListener(() => {
  sendHeartbeat();
  pollLoop();
});

// Kick off immediately on service worker load
pollLoop();
