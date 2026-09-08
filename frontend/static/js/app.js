/**
 * SMARTSCRIBE — MODERN AI WORKSPACE CLIENT ENGINE
 * Single-Page Application Controller & Media Synchronization
 */

// Global Application State
const state = {
  currentView: "dashboard",
  activeItemId: null,
  activeItemData: null,
  isRecording: false,
  recordingTimer: null,
  recordingSeconds: 0,
  audioContext: null,
  audioAnalyser: null,
  audioStream: null,
  animationFrameId: null,
  flashcards: [],
  activeCardIndex: 0,
  quizItems: [],
  selectedFile: null,
  activeNoteId: null,
  notes: [],
  jobs: []
};

// ============================================================================
// INITIALIZATION & LIFECYCLE
// ============================================================================

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initSSE();
  initKeyboardShortcuts();
  initDragAndDrop();
  initAssistantHome();
  loadDashboard();
  loadSystemStatus();
});

// ============================================================================
// NAVIGATION & ROUTER
// ============================================================================

function navigateTo(viewName) {
  state.currentView = viewName;

  // Update sidebar active states
  document.querySelectorAll(".nav-item").forEach(el => {
    el.classList.toggle("active", el.dataset.view === viewName);
  });

  // Switch visible section
  document.querySelectorAll(".view-section").forEach(sec => {
    sec.classList.remove("active");
  });
  const target = document.getElementById(`view-${viewName}`);
  if (target) {
    target.classList.add("active");
  }

  // Update Top Header Page Title
  const titles = {
    dashboard: "Dashboard",
    recorder: "Live Studio",
    import: "Import Media",
    library: "Content Library",
    workspaces: "Knowledge Workspaces",
    video: "Media Intelligence Workspace",
    transform: "Magic Transform Studio",
    search: '"Find It" Global Deep Search',
    study: "Interactive Study Mode",
    teach: '"Teach Me" Socratic Tutor',
    coach: "Speaking & Presentation Coach",
    chat: "Ask SmartScribe",
    notes: "Smart Notes",
    jobs: "Processing Center",
    settings: "Settings"
  };
  const titleElem = document.getElementById("page-title");
  if (titleElem) {
    titleElem.textContent = titles[viewName] || "SmartScribe";
  }

  // Auto-expand More Tools drawer if a secondary tool view is targeted
  const secondaryViews = ["recorder", "import", "video", "transform", "chat", "search", "study", "teach", "coach", "jobs", "settings"];
  const drawer = document.getElementById("more-tools-drawer");
  const arrow = document.getElementById("more-tools-arrow");
  if (secondaryViews.includes(viewName) && drawer && drawer.classList.contains("collapsed")) {
    drawer.classList.remove("collapsed");
    if (arrow) arrow.style.transform = "rotate(180deg)";
  }

  // Hook view-specific data loaders
  if (viewName === "dashboard") {
    initAssistantHome();
    loadDashboard();
  }
  if (viewName === "library") loadLibrary();
  if (viewName === "jobs") loadJobs();
  if (viewName === "notes") loadNotes();
  if (viewName === "workspaces") loadWorkspaces();
  if (viewName === "search") {
    setTimeout(() => document.getElementById("deep-search-input")?.focus(), 50);
  }
  if (viewName === "study" && state.activeItemId) loadStudyMode();
  if (viewName === "chat" && state.activeItemId) loadChatMessages();
}

// ============================================================================
// THEME SWITCHER
// ============================================================================

function initTheme() {
  const saved = localStorage.getItem("smartscribe-theme") || "dark";
  document.documentElement.setAttribute("data-theme", saved);

  const toggleBtn = document.getElementById("theme-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("smartscribe-theme", next);
      showToast(`Switched to ${next} theme`);
    });
  }

  // Sidebar toggle
  const sbToggle = document.getElementById("sidebar-toggle");
  const sidebar = document.getElementById("app-sidebar");
  if (sbToggle && sidebar) {
    sbToggle.addEventListener("click", () => {
      sidebar.classList.toggle("collapsed");
      sbToggle.textContent = sidebar.classList.contains("collapsed") ? "▶" : "◀";
    });
  }
}

// ============================================================================
// SERVER-SENT EVENTS (SSE) REAL-TIME PIPELINE
// ============================================================================

function initSSE() {
  const evtSource = new EventSource("/api/stream/events");

  evtSource.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      handleServerEvent(payload);
    } catch (err) {
      console.warn("SSE parse error:", err);
    }
  };

  evtSource.onerror = () => {
    console.log("SSE disconnected. Reconnecting...");
  };
}

function handleServerEvent(msg) {
  const { event, data } = msg;

  if (event === "job_progress") {
    updateJobIndicator(data);
    if (state.currentView === "jobs") loadJobs();
  } else if (event === "job_completed") {
    showToast(`"${data.title}" completed processing!`);
    hideJobIndicator();
    loadDashboard();
    if (state.currentView === "library") loadLibrary();
    if (state.currentView === "jobs") loadJobs();
    if (state.activeItemId === data.item_id) loadItemIntoWorkspace(data.item_id);
  } else if (event === "job_cancelled") {
    showToast(`Processing cancelled for "${data.title}"`);
    hideJobIndicator();
    if (state.currentView === "jobs") loadJobs();
  } else if (event === "job_failed") {
    showToast(`Processing failed for "${data.title}"`, "error");
    hideJobIndicator();
    if (state.currentView === "jobs") loadJobs();
  }
}

function updateJobIndicator(job) {
  const pill = document.getElementById("header-job-pill");
  const txt = document.getElementById("header-job-text");
  if (pill && txt) {
    pill.style.display = "flex";
    txt.textContent = `${job.title} — ${job.stage} (${job.progress}%)`;
  }
}

function hideJobIndicator() {
  const pill = document.getElementById("header-job-pill");
  if (pill) pill.style.display = "none";
}

// ============================================================================
// DASHBOARD
// ============================================================================

async function loadDashboard() {
  try {
    const res = await fetch("/api/items?limit=4");
    const json = await res.json();
    const items = json.items || [];

    const container = document.getElementById("dashboard-recent-items");
    if (!container) return;

    if (items.length === 0) {
      container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No sessions yet. Click 'Record Meeting' or 'Import Video' above.</p>`;
    } else {
      container.innerHTML = items.map(i => `
        <div class="item-card" onclick="openItem(${i.id})">
          <div class="item-left">
            <span class="type-badge type-${i.type}">${i.type}</span>
            <div class="item-info">
              <div class="item-title">${escapeHtml(i.title)}</div>
              <div class="item-meta">
                <span>${formatDuration(i.duration)}</span>
                <span>•</span>
                <span>${i.created_at}</span>
                <span>•</span>
                <span>${i.status}</span>
              </div>
            </div>
          </div>
          <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.8rem;">Open →</button>
        </div>
      `).join("");
    }

    // Stats
    const totalCountElem = document.getElementById("stat-total-items");
    if (totalCountElem) totalCountElem.textContent = json.count || items.length;

    // Load jobs widget
    const jobsRes = await fetch("/api/jobs");
    const jobsJson = await jobsRes.json();
    const queueBox = document.getElementById("dashboard-queue-box");
    if (queueBox) {
      const active = (jobsJson.jobs || []).filter(j => j.status === "running");
      if (active.length > 0) {
        queueBox.innerHTML = active.map(j => `
          <div style="margin-bottom: 8px;">
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: 600;">
              <span>${escapeHtml(j.title)}</span>
              <span>${j.progress}%</span>
            </div>
            <div style="color: var(--text-muted); font-size: 0.78rem;">${j.details}</div>
          </div>
        `).join("");
      } else {
        queueBox.innerHTML = `<p style="color: var(--text-muted); font-size: 0.88rem;">No active processing tasks.</p>`;
      }
    }
  } catch (err) {
    console.error("Failed to load dashboard:", err);
  }
}

// ============================================================================
// LIVE STUDIO RECORDER & WAVEFORM
// ============================================================================

async function toggleRecording() {
  if (!state.isRecording) {
    startRecordingFlow();
  } else {
    stopRecordingFlow();
  }
}

async function startRecordingFlow() {
  try {
    const res = await fetch("/api/record/start", { method: "POST" });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Failed to start recording");

    state.isRecording = true;
    state.recordingSeconds = 0;

    // UI Updates
    document.getElementById("record-btn-icon").textContent = "■";
    document.getElementById("record-btn-label").textContent = "Stop & Analyze";
    document.getElementById("btn-toggle-record").classList.add("recording");
    document.getElementById("btn-discard-record").style.display = "inline-flex";
    document.getElementById("studio-status-badge").textContent = "Recording in progress";
    document.getElementById("studio-status-badge").className = "type-badge type-video";
    document.getElementById("studio-hint").textContent = "Speak clearly. Click 'Stop & Analyze' when finished.";

    // Start timer
    state.recordingTimer = setInterval(() => {
      state.recordingSeconds++;
      const m = String(Math.floor(state.recordingSeconds / 60)).padStart(2, "0");
      const s = String(state.recordingSeconds % 60).padStart(2, "0");
      document.getElementById("studio-timer").textContent = `${m}:${s}`;
    }, 1000);

    // Start Web Audio Waveform visualizer
    initWaveformVisualizer();
    showToast("Recording started!");

  } catch (err) {
    showToast(err.message, "error");
  }
}

async function stopRecordingFlow() {
  clearInterval(state.recordingTimer);
  stopWaveformVisualizer();

  const title = prompt("Enter a title for this recorded session:", `Session ${new Date().toLocaleString()}`);
  if (title === null) return;

  try {
    const res = await fetch("/api/record/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, type: "meeting" })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Failed to stop recording");

    state.isRecording = false;
    resetStudioUI();
    showToast("Processing queued! Transcription starting in background...");
    navigateTo("jobs");

  } catch (err) {
    showToast(err.message, "error");
  }
}

async function discardRecording() {
  if (!confirm("Are you sure you want to discard this live recording?")) return;
  clearInterval(state.recordingTimer);
  stopWaveformVisualizer();

  await fetch("/api/record/cancel", { method: "POST" });
  state.isRecording = false;
  resetStudioUI();
  showToast("Recording discarded.");
}

function resetStudioUI() {
  document.getElementById("record-btn-icon").textContent = "●";
  document.getElementById("record-btn-label").textContent = "Start Recording";
  document.getElementById("btn-toggle-record").classList.remove("recording");
  document.getElementById("btn-discard-record").style.display = "none";
  document.getElementById("studio-timer").textContent = "00:00";
  document.getElementById("studio-status-badge").textContent = "Microphone Ready";
  document.getElementById("studio-status-badge").className = "type-badge type-meeting";
  document.getElementById("studio-hint").textContent = "Click Start to capture your microphone with real-time waveform.";
}

// Web Audio API Waveform Visualizer
async function initWaveformVisualizer() {
  const canvas = document.getElementById("waveform-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  canvas.width = canvas.parentElement.clientWidth;
  canvas.height = canvas.parentElement.clientHeight;

  try {
    state.audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    state.audioAnalyser = state.audioContext.createAnalyser();
    state.audioAnalyser.fftSize = 64;

    const source = state.audioContext.createMediaStreamSource(state.audioStream);
    source.connect(state.audioAnalyser);

    const bufferLength = state.audioAnalyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function draw() {
      if (!state.isRecording) return;
      state.animationFrameId = requestAnimationFrame(draw);

      state.audioAnalyser.getByteFrequencyData(dataArray);

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const barWidth = (canvas.width / bufferLength) * 1.8;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const barHeight = (dataArray[i] / 255) * canvas.height * 0.9;
        ctx.fillStyle = `rgb(99, ${Math.min(255, 102 + dataArray[i])}, 241)`;
        ctx.fillRect(x, (canvas.height - barHeight) / 2, barWidth - 2, barHeight);
        x += barWidth;
      }
    }
    draw();
  } catch (err) {
    console.warn("Could not access microphone visualizer:", err);
  }
}

function stopWaveformVisualizer() {
  if (state.animationFrameId) cancelAnimationFrame(state.animationFrameId);
  if (state.audioStream) state.audioStream.getTracks().forEach(t => t.stop());
  if (state.audioContext) state.audioContext.close();
}

// ============================================================================
// IMPORT MEDIA (FILE & URL)
// ============================================================================

function initDragAndDrop() {
  const dropZone = document.getElementById("drop-zone");
  if (!dropZone) return;

  ["dragenter", "dragover"].forEach(event => {
    dropZone.addEventListener(event, (e) => {
      e.preventDefault();
      dropZone.style.borderColor = "var(--accent-primary)";
      dropZone.style.backgroundColor = "var(--accent-glow)";
    });
  });

  ["dragleave", "drop"].forEach(event => {
    dropZone.addEventListener(event, (e) => {
      e.preventDefault();
      dropZone.style.borderColor = "var(--border-strong)";
      dropZone.style.backgroundColor = "var(--bg-app)";
    });
  });

  dropZone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length > 0) {
      state.selectedFile = e.dataTransfer.files[0];
      document.getElementById("selected-file-name").textContent = `Selected: ${state.selectedFile.name} (${(state.selectedFile.size / (1024*1024)).toFixed(1)} MB)`;
      document.getElementById("import-title-input").value = state.selectedFile.name.replace(/\.[^/.]+$/, "");
    }
  });
}

function handleFileSelect(input) {
  if (input.files.length > 0) {
    state.selectedFile = input.files[0];
    document.getElementById("selected-file-name").textContent = `Selected: ${state.selectedFile.name} (${(state.selectedFile.size / (1024*1024)).toFixed(1)} MB)`;
    document.getElementById("import-title-input").value = state.selectedFile.name.replace(/\.[^/.]+$/, "");
  }
}

async function uploadSelectedFile() {
  if (!state.selectedFile) {
    showToast("Please select a video or audio file to upload", "error");
    return;
  }

  const formData = new FormData();
  formData.append("file", state.selectedFile);
  formData.append("title", document.getElementById("import-title-input").value || state.selectedFile.name);
  formData.append("type", document.getElementById("import-type-select").value || "video");

  showToast("Uploading file to local engine...");

  try {
    const res = await fetch("/api/import/file", {
      method: "POST",
      body: formData
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Upload failed");

    showToast(`✅ "${json.title}" uploaded! Processing started.`);
    state.selectedFile = null;
    document.getElementById("selected-file-name").textContent = "Up to 2GB file size";
    document.getElementById("import-title-input").value = "";
    navigateTo("jobs");

  } catch (err) {
    showToast(err.message, "error");
  }
}

async function importFromUrl() {
  const urlInput = document.getElementById("import-url-input");
  const url = urlInput.value.trim();
  if (!url) {
    showToast("Please enter a video or audio URL", "error");
    return;
  }

  const btn = event && event.target ? event.target : null;
  const originalText = btn ? btn.textContent : "Fetch & Process";
  if (btn) {
    btn.textContent = "⏳ Fetching...";
    btn.disabled = true;
  }

  showToast("Downloading media from URL... Please wait.");

  try {
    const res = await fetch("/api/import/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, type: "video" })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "URL import failed");

    showToast(`✅ "${json.title}" imported! Transcription queued.`);
    urlInput.value = "";
    navigateTo("jobs");

  } catch (err) {
    showToast(err.message, "error");
  } finally {
    if (btn) {
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }
}

// ============================================================================
// CONTENT LIBRARY
// ============================================================================

async function loadLibrary(filterType = "all", searchQuery = "") {
  const container = document.getElementById("library-items-container");
  if (!container) return;

  try {
    const url = `/api/items?type=${filterType}&search=${encodeURIComponent(searchQuery)}`;
    const res = await fetch(url);
    const json = await res.json();
    const items = json.items || [];

    if (items.length === 0) {
      container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No matching sessions found in library.</p>`;
      return;
    }

    container.innerHTML = items.map(i => `
      <div class="item-card" onclick="openItem(${i.id})">
        <div class="item-left">
          <span class="type-badge type-${i.type}">${i.type}</span>
          <div class="item-info">
            <div class="item-title">${escapeHtml(i.title)}</div>
            <div class="item-meta">
              <span>${formatDuration(i.duration)}</span>
              <span>•</span>
              <span>${i.created_at}</span>
              <span>•</span>
              <span style="color: ${i.status === 'completed' ? 'var(--status-success)' : 'var(--accent-primary)'};">${i.status}</span>
            </div>
          </div>
        </div>
        <div style="display: flex; gap: 8px;">
          <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.8rem;" onclick="event.stopPropagation(); openItem(${i.id})">Open Workspace</button>
          <button class="btn btn-danger" style="padding: 4px 8px; font-size: 0.8rem; display: flex; align-items: center;" title="Delete" onclick="event.stopPropagation(); deleteItem(${i.id})">
            <svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
          </button>
        </div>
      </div>
    `).join("");

  } catch (err) {
    container.innerHTML = `<p style="color: var(--status-error);">Failed to load library: ${err.message}</p>`;
  }
}

function filterLibrary(type, btn) {
  document.querySelectorAll("#view-library .tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  loadLibrary(type, document.getElementById("library-search-input").value);
}

function searchLibrary(val) {
  loadLibrary("all", val);
}

async function deleteItem(id) {
  if (!confirm("Are you sure you want to permanently delete this session and its media?")) return;
  try {
    await fetch(`/api/items/${id}`, { method: "DELETE" });
    showToast("Session deleted.");
    loadLibrary();
  } catch (err) {
    showToast("Failed to delete session", "error");
  }
}

function openItem(id) {
  state.activeItemId = id;
  loadItemIntoWorkspace(id);
  navigateTo("video");
}

// ============================================================================
// VIDEO & MEDIA WORKSPACE (SYNCHRONIZATION ENGINE)
// ============================================================================

async function loadItemIntoWorkspace(itemId) {
  try {
    const res = await fetch(`/api/items/${itemId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to load item");

    state.activeItemId = itemId;
    state.activeItemData = data;

    const item = data.item;
    document.getElementById("current-item-title").textContent = item.title;
    document.getElementById("current-item-meta").textContent = `${item.type.toUpperCase()} • ${formatDuration(item.duration)} • ${item.created_at}`;

    // Smart Mode Badge update
    const smartMode = item.smart_mode || item.type || "video";
    const conf = item.smart_mode_confidence || "auto-detected";
    state.currentSmartMode = smartMode;

    const badgeEl = document.getElementById("workspace-smart-badge");
    const confEl = document.getElementById("workspace-smart-conf");
    if (badgeEl) {
      const modeSVGs = {
        lecture: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg><span>Lecture Mode</span>`,
        meeting: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg><span>Meeting Mode</span>`,
        podcast: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg><span>Podcast Mode</span>`,
        interview: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span>Interview Mode</span>`,
        tutorial: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg><span>Tutorial Mode</span>`,
        presentation: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><span>Presentation Mode</span>`,
        video: `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><polygon points="10 8 16 11.5 10 15 10 8"/></svg><span>Video Mode</span>`
      };
      badgeEl.className = `smart-badge ${smartMode}`;
      badgeEl.innerHTML = modeSVGs[smartMode] || `<span>${smartMode.toUpperCase()} Mode</span>`;
    }
    if (confEl) {
      confEl.textContent = `(${conf})`;
    }

    // Setup Video or Audio Player
    const videoContainer = document.getElementById("video-player-container");
    const audioContainer = document.getElementById("audio-player-container");
    const videoPlayer = document.getElementById("main-video-player");
    const audioPlayer = document.getElementById("main-audio-player");

    if (item.media_type && item.media_type.startsWith("video") && data.media_url) {
      videoContainer.style.display = "flex";
      audioContainer.style.display = "none";
      videoPlayer.src = data.media_url;
      bindMediaSync(videoPlayer);
    } else if (data.media_url) {
      videoContainer.style.display = "none";
      audioContainer.style.display = "flex";
      audioPlayer.src = data.media_url;
      document.getElementById("audio-player-title").textContent = item.title;
      bindMediaSync(audioPlayer);
    } else {
      videoContainer.style.display = "none";
      audioContainer.style.display = "flex";
      document.getElementById("audio-player-title").textContent = `${item.title} (No audio file found)`;
    }

    // Render Speakers
    renderWorkspaceSpeakers(data.speakers || []);

    // Render Transcript Segments
    renderWorkspaceTranscript(data.segments || []);

    // Render Insights (Summary, Custom Sections, Key Points)
    document.getElementById("workspace-summary-box").textContent = item.summary || "No summary available.";
    renderCustomModeSections(data.content_intelligence || []);
    document.getElementById("workspace-keypoints-box").innerHTML = item.key_points ? formatBulletPoints(item.key_points) : "No key points recorded.";

    // Render Chapters
    renderWorkspaceChapters(data.chapters || []);

    // Render Bookmarks
    renderWorkspaceBookmarks(data.bookmarks || []);

    // Render Action Items Checklist
    renderWorkspaceActionItems(data.action_items || []);

    // Update session references across suite
    const studyName = document.getElementById("study-session-name");
    if (studyName) studyName.textContent = `Studying: ${item.title}`;
    const chatTitle = document.getElementById("chat-active-item-title");
    if (chatTitle) chatTitle.textContent = `Grounding in: ${item.title}`;
    const transTitle = document.getElementById("transform-active-item-title");
    if (transTitle) transTitle.textContent = `Session: ${item.title}`;

  } catch (err) {
    showToast(err.message, "error");
  }
}

// Media Synchronization (Click-to-Seek & Auto-Scroll Highlight)
function bindMediaSync(mediaElem) {
  mediaElem.ontimeupdate = () => {
    const curTime = mediaElem.currentTime;

    // Highlight active transcript segment
    const segmentEls = document.querySelectorAll(".transcript-segment");
    segmentEls.forEach(el => {
      const start = parseFloat(el.dataset.start);
      const end = parseFloat(el.dataset.end);

      if (curTime >= start && curTime <= end) {
        if (!el.classList.contains("active-segment")) {
          document.querySelectorAll(".transcript-segment.active-segment").forEach(s => s.classList.remove("active-segment"));
          el.classList.add("active-segment");
          el.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }
    });
  };
}

function seekActiveMedia(seconds) {
  const video = document.getElementById("main-video-player");
  const audio = document.getElementById("main-audio-player");
  const target = video.offsetParent !== null ? video : audio;

  if (target) {
    target.currentTime = seconds;
    target.play();
  }
}

function changePlaybackSpeed(speed) {
  const video = document.getElementById("main-video-player");
  const audio = document.getElementById("main-audio-player");
  video.playbackRate = parseFloat(speed);
  audio.playbackRate = parseFloat(speed);
  showToast(`Playback speed set to ${speed}x`);
}

function renderWorkspaceTranscript(segments) {
  const container = document.getElementById("workspace-transcript-list");
  if (!container) return;

  if (segments.length === 0) {
    container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No speech segments recorded.</p>`;
    return;
  }

  container.innerHTML = segments.map(s => `
    <div class="transcript-segment" data-start="${s.start_time}" data-end="${s.end_time}" onclick="seekActiveMedia(${s.start_time})">
      <span class="segment-ts">[${formatTimestamp(s.start_time)}]</span>
      <div class="segment-text">
        <strong style="color: var(--text-secondary); font-size: 0.78rem;">${escapeHtml(s.speaker)}:</strong> ${escapeHtml(s.text)}
      </div>
    </div>
  `).join("");
}

function renderWorkspaceChapters(chapters) {
  const container = document.getElementById("workspace-chapters-list");
  if (!container) return;

  if (chapters.length === 0) {
    container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No chapters generated.</p>`;
    return;
  }

  container.innerHTML = chapters.map(ch => `
    <div class="item-card" style="padding: 12px;" onclick="seekActiveMedia(${ch.start_time})">
      <div>
        <span class="segment-ts">[${formatTimestamp(ch.start_time)}]</span>
        <strong style="margin-left: 8px;">${escapeHtml(ch.title)}</strong>
        <p style="font-size: 0.82rem; color: var(--text-muted); margin-top: 2px;">${escapeHtml(ch.description || '')}</p>
      </div>
      <button class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.75rem;">Jump ⏩</button>
    </div>
  `).join("");
}

function renderWorkspaceBookmarks(bookmarks) {
  const container = document.getElementById("workspace-bookmarks-list");
  if (!container) return;

  if (bookmarks.length === 0) {
    container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No bookmarks saved. Click 'Bookmark' above to save moments.</p>`;
    return;
  }

  container.innerHTML = bookmarks.map(b => `
    <div class="item-card" style="padding: 12px;">
      <div onclick="seekActiveMedia(${b.timestamp})" style="flex: 1; cursor: pointer;">
        <span class="type-badge type-${b.tag}">${b.tag}</span>
        <span class="segment-ts" style="margin-left: 6px;">[${formatTimestamp(b.timestamp)}]</span>
        <strong style="margin-left: 6px;">${escapeHtml(b.title)}</strong>
        <p style="font-size: 0.82rem; color: var(--text-secondary); margin-top: 4px;">${escapeHtml(b.note || '')}</p>
      </div>
      <button class="btn btn-danger" style="padding: 2px 8px; font-size: 0.75rem;" onclick="deleteBookmark(${b.id})">✕</button>
    </div>
  `).join("");
}

function renderWorkspaceActionItems(actions) {
  const container = document.getElementById("workspace-actions-list");
  if (!container) return;

  if (actions.length === 0) {
    container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No action items detected.</p>`;
    return;
  }

  container.innerHTML = actions.map(a => `
    <div class="item-card" style="padding: 10px 14px;">
      <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; flex: 1;">
        <input type="checkbox" ${a.completed ? "checked" : ""} onchange="toggleActionItem(${a.id}, this.checked)" style="accent-color: var(--status-success); width: 18px; height: 18px;">
        <span style="${a.completed ? 'text-decoration: line-through; color: var(--text-muted);' : ''}">${escapeHtml(a.task)}</span>
      </label>
    </div>
  `).join("");
}

function switchWorkspaceTab(tabId, btn) {
  document.querySelectorAll(".intelligence-pane .tab-btn").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");

  ["summary", "chat", "actions", "bookmarks"].forEach(t => {
    const el = document.getElementById(`tab-content-${t}`);
    if (el) el.style.display = t === tabId ? "flex" : "none";
  });
}

function renderWorkspaceSpeakers(speakers) {
  const container = document.getElementById("workspace-speakers-list");
  if (!container) return;

  const pencilSvg = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24" style="margin-left: 4px; opacity: 0.7;"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>`;

  if (!speakers || speakers.length === 0) {
    container.innerHTML = `<span class="speaker-pill" onclick="promptRenameSpeaker('Speaker 1')"><span>Speaker 1</span>${pencilSvg}</span>`;
    return;
  }

  container.innerHTML = speakers.map(s => {
    const name = s.display_name || s.speaker;
    const count = s.segment_count ? ` (${s.segment_count})` : '';
    return `<span class="speaker-pill" onclick="promptRenameSpeaker('${escapeHtml(s.speaker)}')"><span>${escapeHtml(name)}${count}</span>${pencilSvg}</span>`;
  }).join("");
}

function promptRenameSpeaker(speakerName) {
  document.getElementById("rename-speaker-old-name").value = speakerName;
  document.getElementById("rename-speaker-input").value = speakerName;
  document.getElementById("rename-speaker-modal").style.display = "flex";
  document.getElementById("rename-speaker-input").focus();
}

function closeRenameSpeakerModal() {
  document.getElementById("rename-speaker-modal").style.display = "none";
}

async function submitRenameSpeaker() {
  const oldName = document.getElementById("rename-speaker-old-name").value;
  const newName = document.getElementById("rename-speaker-input").value.trim();
  if (!newName || !state.activeItemId) return;

  try {
    const res = await fetch(`/api/items/${state.activeItemId}/speakers/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_name: oldName, new_name: newName })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to rename speaker");

    showToast(`Renamed to '${newName}'`);
    closeRenameSpeakerModal();
    loadItemIntoWorkspace(state.activeItemId);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openChangeSmartModeModal() {
  const modal = document.getElementById("smart-mode-modal");
  if (!modal) return;
  // Pre-check current mode
  const current = state.currentSmartMode || "meeting";
  const radio = modal.querySelector(`input[value="${current}"]`);
  if (radio) radio.checked = true;
  modal.style.display = "flex";
}

function closeSmartModeModal() {
  document.getElementById("smart-mode-modal").style.display = "none";
}

async function submitChangeSmartMode() {
  const modal = document.getElementById("smart-mode-modal");
  const selected = modal.querySelector('input[name="smart_mode_choice"]:checked');
  if (!selected || !state.activeItemId) return;

  const newMode = selected.value;
  const reanalyze = document.getElementById("smart-mode-reanalyze-check")?.checked || false;

  closeSmartModeModal();
  showToast(reanalyze ? `Re-analyzing in ${newMode.toUpperCase()} Mode...` : `Switched to ${newMode.toUpperCase()} Mode`);

  try {
    const res = await fetch(`/api/items/${state.activeItemId}/smart_mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smart_mode: newMode, reanalyze: reanalyze })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to update Smart Mode");

    loadItemIntoWorkspace(state.activeItemId);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderCustomModeSections(sections) {
  const container = document.getElementById("workspace-custom-sections-container");
  if (!container) return;

  if (!sections || sections.length === 0) {
    container.innerHTML = "";
    return;
  }

  container.innerHTML = sections.map(sec => `
    <div style="background: var(--bg-app); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 12px;">
      <h4 style="font-size: 0.88rem; font-weight: 700; margin-bottom: 6px; color: var(--accent-secondary);">${escapeHtml(sec.title)}</h4>
      <div style="font-size: 0.86rem; line-height: 1.6; color: var(--text-primary); white-space: pre-wrap;">${escapeHtml(sec.body)}</div>
    </div>
  `).join("");
}

async function sendMiniChatMessage() {
  const input = document.getElementById("workspace-mini-chat-input");
  const query = input.value.trim();
  if (!query || !state.activeItemId) return;

  input.value = "";
  const chatBox = document.getElementById("workspace-mini-chat-box");

  // User bubble
  const userBubble = document.createElement("div");
  userBubble.className = "chat-bubble user";
  userBubble.style.fontSize = "0.84rem";
  userBubble.style.padding = "8px 12px";
  userBubble.textContent = query;
  chatBox.appendChild(userBubble);
  chatBox.scrollTop = chatBox.scrollHeight;

  // Assistant typing bubble
  const aiBubble = document.createElement("div");
  aiBubble.className = "chat-bubble assistant";
  aiBubble.style.fontSize = "0.84rem";
  aiBubble.style.padding = "8px 12px";
  aiBubble.innerHTML = "Thinking...";
  chatBox.appendChild(aiBubble);
  chatBox.scrollTop = chatBox.scrollHeight;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: state.activeItemId, message: query })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Chat failed");

    // Replace [MM:SS] with clickable seek links
    let formatted = escapeHtml(data.response).replace(/\[(\d{2}:\d{2})\]/g, (match, ts) => {
      const parts = ts.split(":").map(Number);
      const secs = parts[0] * 60 + parts[1];
      return `<button class="segment-ts" onclick="seekActiveMedia(${secs})">[${ts}]</button>`;
    });

    aiBubble.innerHTML = formatted;
    chatBox.scrollTop = chatBox.scrollHeight;
  } catch (err) {
    aiBubble.textContent = "Sorry, could not process that question.";
  }
}

function filterTranscriptSegments(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll(".transcript-segment").forEach(el => {
    const text = el.querySelector(".segment-text").textContent.toLowerCase();
    el.style.display = !q || text.includes(q) ? "flex" : "none";
  });
}

async function addBookmarkPrompt() {
  if (!state.activeItemId) {
    showToast("Please select a session first", "error");
    return;
  }
  const video = document.getElementById("main-video-player");
  const audio = document.getElementById("main-audio-player");
  const player = video.offsetParent !== null ? video : audio;
  const curTime = player ? player.currentTime : 0.0;

  const title = prompt(`Bookmark title for timestamp [${formatTimestamp(curTime)}]:`, "Key Explanation");
  if (!title) return;

  const note = prompt("Optional note:", "");

  await fetch(`/api/items/${state.activeItemId}/bookmarks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ timestamp: curTime, tag: "important", title, note })
  });

  showToast("Bookmark added!");
  loadItemIntoWorkspace(state.activeItemId);
}

async function deleteBookmark(bmId) {
  await fetch(`/api/bookmarks/${bmId}`, { method: "DELETE" });
  showToast("Bookmark deleted.");
  if (state.activeItemId) loadItemIntoWorkspace(state.activeItemId);
}

async function toggleActionItem(actionId, completed) {
  await fetch(`/api/action_items/${actionId}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ completed: completed ? 1 : 0 })
  });
  showToast(completed ? "Task marked complete!" : "Task reopened.");
}

function copyActiveTranscript() {
  const container = document.getElementById("workspace-transcript-list");
  if (!container) return;
  const text = Array.from(container.querySelectorAll(".transcript-segment"))
    .map(el => `${el.querySelector(".segment-ts").textContent} ${el.querySelector(".segment-text").textContent}`)
    .join("\n");
  navigator.clipboard.writeText(text);
  showToast("Transcript copied to clipboard!");
}

// ============================================================================
// INTERACTIVE STUDY MODE (3D FLASHCARDS & QUIZZES)
// ============================================================================

async function loadStudyMode() {
  if (!state.activeItemId) return;

  try {
    const res = await fetch(`/api/items/${state.activeItemId}`);
    const json = await res.json();
    const materials = json.study_materials || [];

    const flashcardsObj = materials.find(m => m.type === "flashcards");
    const quizObj = materials.find(m => m.type === "quiz");
    const cramObj = materials.find(m => m.type === "cram_sheet");

    state.flashcards = (flashcardsObj && flashcardsObj.data) ? flashcardsObj.data : [];
    state.activeCardIndex = 0;
    renderActiveFlashcard();

    renderQuizQuestions((quizObj && quizObj.data) ? quizObj.data : []);
    renderCramSheet((cramObj && cramObj.data) ? cramObj.data : []);

  } catch (err) {
    console.error("Failed to load study mode:", err);
  }
}

function switchStudyTab(subtab, btn) {
  document.querySelectorAll("#view-study .tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");

  ["flashcards", "quiz", "cram"].forEach(t => {
    const el = document.getElementById(`study-subtab-${t}`);
    if (el) el.style.display = t === subtab ? "block" : "none";
  });
}

function flipActiveCard() {
  const card = document.getElementById("main-flashcard");
  if (card) card.classList.toggle("is-flipped");
}

function renderActiveFlashcard() {
  const front = document.getElementById("flashcard-front-text");
  const back = document.getElementById("flashcard-back-text");
  const counter = document.getElementById("flashcard-counter");
  const card = document.getElementById("main-flashcard");

  if (card) card.classList.remove("is-flipped");

  if (state.flashcards.length === 0) {
    front.textContent = "No flashcards generated yet. Click 'Generate Study Set' above.";
    back.textContent = "Click to flip.";
    counter.textContent = "0 / 0";
    return;
  }

  const cur = state.flashcards[state.activeCardIndex];
  front.textContent = cur.question;
  back.textContent = cur.answer;
  counter.textContent = `${state.activeCardIndex + 1} / ${state.flashcards.length}`;
}

function nextFlashcard() {
  if (state.flashcards.length > 0) {
    state.activeCardIndex = (state.activeCardIndex + 1) % state.flashcards.length;
    renderActiveFlashcard();
  }
}

function prevFlashcard() {
  if (state.flashcards.length > 0) {
    state.activeCardIndex = (state.activeCardIndex - 1 + state.flashcards.length) % state.flashcards.length;
    renderActiveFlashcard();
  }
}

function renderQuizQuestions(quizList) {
  const container = document.getElementById("quiz-questions-list");
  if (!container) return;

  if (!quizList || quizList.length === 0) {
    container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem;">No quiz generated for this lecture yet. Click 'Generate Study Set' above.</p>`;
    return;
  }

  container.innerHTML = quizList.map((q, idx) => `
    <div class="quiz-card" id="quiz-q-${idx}">
      <div class="quiz-question-title">Q${idx + 1}. ${escapeHtml(q.question)}</div>
      <div class="quiz-options-list">
        ${q.options.map((opt, optIdx) => `
          <div class="quiz-option" onclick="validateQuizAnswer(${idx}, ${optIdx}, ${q.answer}, '${escapeHtml(q.explanation || '')}')">
            <span>${String.fromCharCode(65 + optIdx)}.</span>
            <span>${escapeHtml(opt)}</span>
          </div>
        `).join("")}
      </div>
      <div class="quiz-explanation-box" id="quiz-exp-${idx}" style="display: none;"></div>
    </div>
  `).join("");
}

function validateQuizAnswer(qIdx, selectedIdx, correctIdx, explanation) {
  const card = document.getElementById(`quiz-q-${qIdx}`);
  if (!card) return;

  const options = card.querySelectorAll(".quiz-option");
  options.forEach((opt, idx) => {
    opt.onclick = null; // Disable further clicks
    if (idx === correctIdx) {
      opt.classList.add("correct");
    } else if (idx === selectedIdx) {
      opt.classList.add("incorrect");
    }
  });

  const expBox = document.getElementById(`quiz-exp-${qIdx}`);
  if (expBox) {
    expBox.style.display = "block";
    expBox.innerHTML = `<strong>${selectedIdx === correctIdx ? "✅ Correct!" : "❌ Incorrect."}</strong> ${explanation}`;
  }
}

function renderCramSheet(cramPoints) {
  const container = document.getElementById("cram-sheet-list");
  if (!container) return;

  if (!cramPoints || cramPoints.length === 0) {
    container.innerHTML = `<li style="color: var(--text-muted);">No cram sheet points available.</li>`;
    return;
  }

  container.innerHTML = cramPoints.map(p => `<li>${escapeHtml(p)}</li>`).join("");
}

async function generateStudySetForActiveItem() {
  if (!state.activeItemId) {
    showToast("Please open a session first", "error");
    return;
  }
  showToast("Generating flashcards and quiz questions with local AI...");
  try {
    const res = await fetch("/api/study/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: state.activeItemId })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Study generation failed");

    showToast("✅ Study set generated successfully!");
    loadStudyMode();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ============================================================================
// ASK SMARTSCRIBE (GROUNDED AI CHAT)
// ============================================================================

async function loadChatMessages() {
  if (!state.activeItemId) return;
  const box = document.getElementById("chat-messages-box");
  if (!box) return;

  try {
    const res = await fetch(`/api/items/${state.activeItemId}`);
    const data = await res.json();
    const history = data.chat_history || [];

    if (history.length === 0) {
      box.innerHTML = `
        <div class="chat-bubble assistant">
          Hello! I am SmartScribe AI. Ask me anything about "${escapeHtml(data.item.title)}". I will ground my answers strictly in the content and cite exact timestamps.
        </div>
      `;
      return;
    }

    box.innerHTML = history.map(m => `
      <div class="chat-bubble ${m.role}">
        ${escapeHtml(m.content)}
        ${m.citations && m.citations.length > 0 ? `
          <div style="margin-top: 6px;">
            ${m.citations.map(c => `<span class="citation-pill" onclick="seekActiveMedia(${c.seconds}); navigateTo('video');">${c.label}</span>`).join("")}
          </div>
        ` : ''}
      </div>
    `).join("");

    box.scrollTop = box.scrollHeight;

  } catch (err) {
    console.error("Failed to load chat:", err);
  }
}

async function sendChatMessage() {
  const input = document.getElementById("chat-user-input");
  const text = input.value.trim();
  if (!text) return;

  if (!state.activeItemId) {
    showToast("Please select a session from Library first", "error");
    return;
  }

  input.value = "";
  const box = document.getElementById("chat-messages-box");

  // Add user message bubble
  box.innerHTML += `<div class="chat-bubble user">${escapeHtml(text)}</div>`;
  box.innerHTML += `<div class="chat-bubble assistant" id="chat-loading-bubble">Thinking with local Qwen...</div>`;
  box.scrollTop = box.scrollHeight;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: state.activeItemId, message: text })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Chat failed");

    const loadingBubble = document.getElementById("chat-loading-bubble");
    if (loadingBubble) loadingBubble.remove();

    const citationHtml = data.citations && data.citations.length > 0 ? `
      <div style="margin-top: 6px;">
        ${data.citations.map(c => `<span class="citation-pill" onclick="seekActiveMedia(${c.seconds}); navigateTo('video');">${c.label}</span>`).join("")}
      </div>
    ` : '';

    box.innerHTML += `
      <div class="chat-bubble assistant">
        ${escapeHtml(data.reply)}
        ${citationHtml}
      </div>
    `;
    box.scrollTop = box.scrollHeight;

  } catch (err) {
    const loadingBubble = document.getElementById("chat-loading-bubble");
    if (loadingBubble) loadingBubble.textContent = `Error: ${err.message}`;
  }
}

function sendPresetPrompt(promptText) {
  document.getElementById("chat-user-input").value = promptText;
  sendChatMessage();
}

function clearChatHistory() {
  document.getElementById("chat-messages-box").innerHTML = `
    <div class="chat-bubble assistant">Chat cleared. Ask a new question!</div>
  `;
}

// ============================================================================
// ✨ MAGIC TRANSFORM STUDIO
// ============================================================================

state.selectedTransformFormat = "executive_briefing";

function switchTransformDomain(domain, btn) {
  document.querySelectorAll("#view-transform .tab-btn").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");

  ["understand", "learn", "create", "act"].forEach(d => {
    const el = document.getElementById(`domain-${d}`);
    if (el) el.style.display = d === domain ? "grid" : "none";
  });
}

function selectTransformFormat(format, card) {
  state.selectedTransformFormat = format;
  document.querySelectorAll(".transform-card").forEach(c => c.style.borderColor = "var(--border-subtle)");
  if (card) card.style.borderColor = "var(--accent-primary)";
  showToast(`Selected format: ${format.replace(/_/g, ' ').toUpperCase()}`);
}

async function executeMagicTransform() {
  if (!state.activeItemId) {
    showToast("Please open a media session from the Library first", "error");
    return;
  }

  const format = state.selectedTransformFormat || "executive_briefing";
  const tone = document.getElementById("transform-tone-select").value;
  const length = document.getElementById("transform-length-select").value;
  const outputBox = document.getElementById("transform-output-box");
  const titleBox = document.getElementById("transform-output-title");
  const btn = document.getElementById("transform-submit-btn");

  outputBox.textContent = `Transforming transcript into ${format.replace(/_/g, ' ')}... Please wait...`;
  btn.disabled = true;
  btn.textContent = "⏳ Transforming...";
  showToast(`Generating ${format.replace(/_/g, ' ')} with local AI...`);

  try {
    const res = await fetch("/api/transform", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: state.activeItemId,
        target_format: format,
        tone: tone,
        length: length
      })
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Transformation failed");

    outputBox.textContent = json.content;
    if (titleBox) titleBox.textContent = `${format.replace(/_/g, ' ').toUpperCase()} DELIVERABLE`;
    showToast("✨ Content transformed successfully!");

  } catch (err) {
    outputBox.textContent = `Error: ${err.message}`;
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Transform Active Session";
  }
}

function copyTransformOutput() {
  const text = document.getElementById("transform-output-box").textContent;
  navigator.clipboard.writeText(text);
  showToast("Deliverable copied to clipboard!");
}

async function saveTransformAsNote() {
  const content = document.getElementById("transform-output-box").textContent;
  const title = `Transform: ${state.selectedTransformFormat || 'Deliverable'} (${new Date().toLocaleDateString()})`;
  if (!content || content.startsWith("Select a format")) {
    showToast("Generate a transform first before saving as a note", "error");
    return;
  }

  try {
    const res = await fetch("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: state.activeItemId,
        title: title,
        content: content,
        tags: "transform, deliverable"
      })
    });
    if (!res.ok) throw new Error("Failed to save note");
    showToast("Saved to Smart Notes!");
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ============================================================================
// "FIND IT" GLOBAL DEEP SEARCH
// ============================================================================

state.deepSearchFilter = "all";

function setDeepSearchFilter(type, btn) {
  state.deepSearchFilter = type;
  document.querySelectorAll("#view-search .tab-btn").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  executeDeepSearch();
}

async function executeDeepSearch() {
  const input = document.getElementById("deep-search-input");
  const query = input.value.trim();
  const container = document.getElementById("deep-search-results-box");
  if (!query) {
    container.innerHTML = `<p style="color: var(--text-muted); font-size: 0.9rem; text-align: center; padding: 30px;">Type a term above to search across every spoken second of your library.</p>`;
    return;
  }

  container.innerHTML = `<p style="color: var(--text-muted); text-align: center; padding: 20px;">Searching transcript index...</p>`;

  try {
    const res = await fetch(`/api/search/deep?q=${encodeURIComponent(query)}&type=${state.deepSearchFilter}`);
    const data = await res.json();
    if (!res.ok) throw new Error("Search failed");

    if (data.total_count === 0) {
      container.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--text-muted);">
        <div style="margin-bottom: 10px;"><svg class="svg-icon svg-xl" viewBox="0 0 24 24" style="color: var(--text-muted);"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></div>
        No spoken words or notes matched "<strong>${escapeHtml(query)}</strong>".
      </div>`;
      return;
    }

    let html = `<div style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 4px;">Found ${data.total_count} matches for "${escapeHtml(query)}":</div>`;

    // Render segment matches
    if (data.segments && data.segments.length > 0) {
      html += data.segments.map(s => {
        // Highlight keyword in text
        const regex = new RegExp(`(${escapeRegex(query)})`, "gi");
        const highlighted = escapeHtml(s.text).replace(regex, "<mark>$1</mark>");
        return `
          <div class="deep-search-result" onclick="jumpToSearchResult(${s.item_id}, ${s.start_time})">
            <div class="deep-search-meta">
              <strong>${escapeHtml(s.item_title)}</strong>
              <span class="segment-ts">[${formatTimestamp(s.start_time)}]</span>
            </div>
            <div class="deep-search-snippet">
              <span style="color: var(--accent-secondary); font-weight: 600; font-size: 0.8rem;">${escapeHtml(s.speaker)}:</span> ${highlighted}
            </div>
          </div>
        `;
      }).join("");
    }

    // Render item matches
    if (data.items && data.items.length > 0) {
      html += data.items.map(it => `
        <div class="deep-search-result" onclick="openItem(${it.id})">
          <div class="deep-search-meta">
            <span class="type-badge type-${it.smart_mode || it.type}">${(it.smart_mode || it.type).toUpperCase()}</span>
            <span>${it.created_at}</span>
          </div>
          <strong style="font-size: 1rem;">${escapeHtml(it.title)}</strong>
          <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 4px;">${escapeHtml((it.summary || '').slice(0, 160))}...</p>
        </div>
      `).join("");
    }

    container.innerHTML = html;

  } catch (err) {
    container.innerHTML = `<p style="color: var(--status-error); text-align: center;">Error: ${err.message}</p>`;
  }
}

function jumpToSearchResult(itemId, timestamp) {
  openItem(itemId);
  setTimeout(() => {
    seekActiveMedia(timestamp);
  }, 400);
}

function escapeRegex(string) {
  return string.replace(/[/\-\\^$*+?.()|[\]{}]/g, '\\$&');
}

// ============================================================================
// "TEACH ME" SOCRATIC TUTOR
// ============================================================================

state.teachStepIndex = 1;

async function startTeachSession() {
  if (!state.activeItemId) {
    showToast("Please select a lecture or video from the Library first", "error");
    return;
  }

  state.teachStepIndex = 1;
  const box = document.getElementById("teach-messages-box");
  const level = document.getElementById("teach-level-select").value;

  box.innerHTML = `<div class="chat-bubble assistant">Starting ${level.toUpperCase()} lesson from this session... Preparing concept 1...</div>`;

  try {
    const res = await fetch("/api/teach/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: state.activeItemId,
        level: level,
        step_index: 1
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to start lesson");

    box.innerHTML = `<div class="chat-bubble assistant">${escapeHtml(data.response)}</div>`;
  } catch (err) {
    box.innerHTML = `<div class="chat-bubble assistant">Error: ${err.message}</div>`;
  }
}

async function submitTeachAnswer() {
  const input = document.getElementById("teach-user-input");
  const answer = input.value.trim();
  if (!answer || !state.activeItemId) return;

  input.value = "";
  const box = document.getElementById("teach-messages-box");
  const level = document.getElementById("teach-level-select").value;

  // Append user bubble
  box.innerHTML += `<div class="chat-bubble user">${escapeHtml(answer)}</div>`;
  box.innerHTML += `<div class="chat-bubble assistant" id="teach-loading-bubble">Evaluating and preparing next concept...</div>`;
  box.scrollTop = box.scrollHeight;

  state.teachStepIndex += 1;

  try {
    const res = await fetch("/api/teach/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: state.activeItemId,
        level: level,
        user_reply: answer,
        step_index: state.teachStepIndex
      })
    });
    const data = await res.json();
    const loading = document.getElementById("teach-loading-bubble");
    if (loading) loading.remove();

    if (!res.ok) throw new Error(data.error || "Failed step");

    box.innerHTML += `<div class="chat-bubble assistant">${escapeHtml(data.response)}</div>`;
    box.scrollTop = box.scrollHeight;
  } catch (err) {
    const loading = document.getElementById("teach-loading-bubble");
    if (loading) loading.textContent = `Error: ${err.message}`;
  }
}

// ============================================================================
// SPEAKING & PRESENTATION COACH
// ============================================================================

async function analyzeActiveSessionWithCoach() {
  if (!state.activeItemId) {
    showToast("Please select a session from the Library first", "error");
    return;
  }

  showToast("Analyzing speech pace, fillers, and clarity...");
  const feedbackBox = document.getElementById("coach-feedback-box");
  feedbackBox.textContent = "AI Coach is evaluating speaking cadence and filler patterns... Please wait...";

  try {
    const res = await fetch("/api/coach/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: state.activeItemId,
        title: state.activeItemData?.item?.title || "Speech Session"
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Speech analysis failed");

    const r = data.report;
    document.getElementById("coach-wpm-val").textContent = `${r.wpm} WPM`;
    document.getElementById("coach-pace-status").textContent = `${r.pace_rating} — ${r.pace_note}`;
    document.getElementById("coach-fillers-val").textContent = r.filler_count;

    const breakdownText = Object.entries(r.filler_breakdown || {})
      .filter(([k, v]) => v > 0)
      .map(([k, v]) => `${k}: ${v}`)
      .join(" • ") || "None detected!";
    document.getElementById("coach-fillers-breakdown").textContent = breakdownText;

    document.getElementById("coach-duration-val").textContent = formatDuration(r.duration);
    document.getElementById("coach-words-count").textContent = `${r.word_count} words spoken`;

    feedbackBox.textContent = r.coach_feedback;
    showToast("Speech Coaching Report ready!");

  } catch (err) {
    feedbackBox.textContent = `Error: ${err.message}`;
    showToast(err.message, "error");
  }
}

// ============================================================================
// WORKSPACES MANAGER
// ============================================================================

async function loadWorkspaces() {
  const container = document.getElementById("workspaces-grid-box");
  if (!container) return;

  try {
    const res = await fetch("/api/workspaces");
    const data = await res.json();
    state.workspaces = data.workspaces || [];

    if (state.workspaces.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; padding: 40px; color: var(--text-muted); grid-column: 1 / -1;">
          <div style="margin-bottom: 12px;"><svg class="svg-icon svg-xl" viewBox="0 0 24 24" style="color: var(--accent-violet);"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg></div>
          <p>No workspaces created yet.</p>
          <button class="btn btn-primary" style="margin-top: 10px;" onclick="openNewWorkspaceModal()">+ Create Workspace</button>
        </div>
      `;
      return;
    }

    container.innerHTML = state.workspaces.map(ws => `
      <div class="item-card" style="padding: 20px; display: flex; flex-direction: column;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
          <div class="icon-box icon-box-violet" style="width: 32px; height: 32px;">
            <svg class="svg-icon svg-sm" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>
          </div>
          <button class="btn btn-danger" style="padding: 2px 8px; font-size: 0.72rem;" onclick="deleteWorkspace(${ws.id})">Delete</button>
        </div>
        <strong style="font-size: 1.05rem; margin-bottom: 4px;">${escapeHtml(ws.name)}</strong>
        <p style="font-size: 0.82rem; color: var(--text-secondary); margin-bottom: 12px; flex: 1;">${escapeHtml(ws.description || 'No description')}</p>
        <div style="font-size: 0.78rem; color: var(--text-muted);">${ws.item_count || 0} media sessions</div>
      </div>
    `).join("");

  } catch (err) {
    container.innerHTML = `<p style="color: var(--status-error);">Failed to load workspaces.</p>`;
  }
}

function openNewWorkspaceModal() {
  document.getElementById("new-workspace-modal").style.display = "flex";
  document.getElementById("new-workspace-name").focus();
}

function closeNewWorkspaceModal() {
  document.getElementById("new-workspace-modal").style.display = "none";
}

async function submitCreateWorkspace() {
  const name = document.getElementById("new-workspace-name").value.trim();
  const desc = document.getElementById("new-workspace-desc").value.trim();
  if (!name) return;

  try {
    const res = await fetch("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, description: desc })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to create workspace");

    showToast(`Workspace '${name}' created!`);
    closeNewWorkspaceModal();
    loadWorkspaces();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteWorkspace(id) {
  if (!confirm("Are you sure you want to delete this workspace? Items will not be deleted.")) return;
  try {
    await fetch(`/api/workspaces/${id}`, { method: "DELETE" });
    showToast("Workspace removed.");
    loadWorkspaces();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// ============================================================================
// SMART NOTES
// ============================================================================

async function loadNotes() {
  const list = document.getElementById("notes-sidebar-list");
  if (!list) return;

  try {
    const res = await fetch(`/api/items/${state.activeItemId || ''}/notes`);
    // Fallback if no item selected
    const notesRes = await fetch("/api/items");
    const json = await (await fetch("/api/items/1")).json().catch(() => ({ notes: [] }));
    const notes = json.notes || [];
    state.notes = notes;

    if (notes.length === 0) {
      list.innerHTML = `<p style="color: var(--text-muted); font-size: 0.85rem;">No notes yet. Click '+ New' above.</p>`;
      return;
    }

    list.innerHTML = notes.map(n => `
      <div class="command-item ${n.id === state.activeNoteId ? 'selected' : ''}" onclick="selectNote(${n.id})">
        <strong>${escapeHtml(n.title)}</strong>
      </div>
    `).join("");

  } catch (err) {
    list.innerHTML = `<p style="color: var(--text-muted); font-size: 0.85rem;">No notes created yet.</p>`;
  }
}

function createNewNote() {
  state.activeNoteId = null;
  document.getElementById("note-editor-title").value = "Untitled Note";
  document.getElementById("note-editor-content").value = "";
}

function selectNote(noteId) {
  const n = state.notes.find(x => x.id === noteId);
  if (!n) return;
  state.activeNoteId = noteId;
  document.getElementById("note-editor-title").value = n.title;
  document.getElementById("note-editor-content").value = n.content;
}

async function saveActiveNote() {
  const title = document.getElementById("note-editor-title").value.trim() || "Untitled Note";
  const content = document.getElementById("note-editor-content").value;

  if (state.activeNoteId) {
    await fetch(`/api/notes/${state.activeNoteId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, content })
    });
    showToast("Note updated!");
  } else if (state.activeItemId) {
    await fetch(`/api/items/${state.activeItemId}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, content })
    });
    showToast("New note created!");
  }
  loadNotes();
}

async function deleteActiveNote() {
  if (!state.activeNoteId) return;
  if (!confirm("Delete this note?")) return;
  await fetch(`/api/notes/${state.activeNoteId}`, { method: "DELETE" });
  showToast("Note deleted.");
  createNewNote();
  loadNotes();
}

// ============================================================================
// PROCESSING QUEUE
// ============================================================================

async function loadJobs() {
  const tbody = document.getElementById("jobs-table-body");
  if (!tbody) return;

  try {
    const res = await fetch("/api/jobs");
    const json = await res.json();
    const jobs = json.jobs || [];

    if (jobs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="padding: 24px; text-align: center; color: var(--text-muted);">No background tasks in queue.</td></tr>`;
      return;
    }

    tbody.innerHTML = jobs.map(j => `
      <tr style="border-bottom: 1px solid var(--border-subtle);">
        <td style="padding: 12px 16px; font-weight: 600;">${escapeHtml(j.title)}</td>
        <td style="padding: 12px 16px; color: var(--text-secondary); text-transform: capitalize;">${j.stage}</td>
        <td style="padding: 12px 16px;">
          <div style="background: var(--bg-app); border-radius: var(--radius-full); height: 8px; overflow: hidden; margin-bottom: 4px;">
            <div style="background: var(--accent-primary); height: 100%; width: ${j.progress}%; transition: width 0.3s ease;"></div>
          </div>
          <span style="font-size: 0.75rem; color: var(--text-muted);">${j.details}</span>
        </td>
        <td style="padding: 12px 16px;">
          <span class="type-badge ${j.status === 'completed' ? 'type-lecture' : (j.status === 'failed' ? 'type-record' : 'type-video')}">${j.status}</span>
        </td>
        <td style="padding: 12px 16px; text-align: right;">
          ${j.status === 'running' || j.status === 'queued' ? `
            <button class="btn btn-danger" style="padding: 4px 10px; font-size: 0.8rem;" onclick="cancelJob(${j.id})">Cancel</button>
          ` : '-'}
        </td>
      </tr>
    `).join("");

  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="color: var(--status-error); padding: 16px;">Error loading queue: ${err.message}</td></tr>`;
  }
}

async function cancelJob(jobId) {
  await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
  showToast("Cancellation signal sent.");
  loadJobs();
}

// ============================================================================
// SETTINGS & SYSTEM STATUS
// ============================================================================

async function loadSystemStatus() {
  try {
    const res = await fetch("/api/system/status");
    const data = await res.json();

    const threadsElem = document.getElementById("settings-threads-val");
    if (threadsElem) threadsElem.textContent = `${data.llm_threads} Threads (${data.cpu_cores} Cores available)`;

    const deviceElem = document.getElementById("settings-device-val");
    if (deviceElem) deviceElem.textContent = `${data.whisper_device.toUpperCase()} (Optimal)`;

  } catch (err) {
    console.warn("Could not load system status:", err);
  }
}

// ============================================================================
// EXPORT SYSTEM (MARKDOWN, JSON, TXT)
// ============================================================================

function openExportModal() {
  if (!state.activeItemId) {
    showToast("Please open a session first", "error");
    return;
  }
  const choice = prompt("Select Export Format:\n1 = Markdown (.md)\n2 = Plain Text (.txt)\n3 = JSON (.json)", "1");
  const formats = { "1": "md", "2": "txt", "3": "json" };
  const fmt = formats[choice];
  if (!fmt) return;

  window.open(`/api/export/${state.activeItemId}/${fmt}`, "_blank");
  showToast(`Exporting ${fmt.toUpperCase()}...`);
}

// ============================================================================
// COMMAND PALETTE (Ctrl + K)
// ============================================================================

function initKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // Ctrl + K or Cmd + K
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openCommandPalette();
    }
    // Escape
    if (e.key === "Escape") {
      closeCommandPalette();
    }
    // Ctrl + \ to toggle sidebar
    if ((e.ctrlKey || e.metaKey) && e.key === "\\") {
      e.preventDefault();
      document.getElementById("sidebar-toggle").click();
    }
  });
}

function openCommandPalette() {
  const modal = document.getElementById("command-modal-backdrop");
  const input = document.getElementById("command-palette-input");
  if (modal && input) {
    modal.classList.add("open");
    input.value = "";
    input.focus();
    handleCommandSearch("");
  }
}

function closeCommandPalette() {
  const modal = document.getElementById("command-modal-backdrop");
  if (modal) modal.classList.remove("open");
}

async function handleCommandSearch(query) {
  const box = document.getElementById("command-results-box");
  if (!box) return;

  const q = query.toLowerCase().trim();

  // Static Action Commands
  const staticCommands = [
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>`,
      label: "Record Live Studio (Mic)",
      action: () => { closeCommandPalette(); navigateTo("recorder"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
      label: "Import Media (File / URL)",
      action: () => { closeCommandPalette(); navigateTo("import"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3z"/></svg>`,
      label: "Open Magic Transform Studio",
      action: () => { closeCommandPalette(); navigateTo("transform"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
      label: "Find It: Global Deep Search",
      action: () => { closeCommandPalette(); navigateTo("search"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>`,
      label: "Open Interactive Study Mode",
      action: () => { closeCommandPalette(); navigateTo("study"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>`,
      label: "Open 'Teach Me' Socratic Tutor",
      action: () => { closeCommandPalette(); navigateTo("teach"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
      label: "Open Speaking & Presentation Coach",
      action: () => { closeCommandPalette(); navigateTo("coach"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
      label: "Open Ask SmartScribe (AI Chat)",
      action: () => { closeCommandPalette(); navigateTo("chat"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
      label: "View Knowledge Workspaces",
      action: () => { closeCommandPalette(); navigateTo("workspaces"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>`,
      label: "View Background Processing Center",
      action: () => { closeCommandPalette(); navigateTo("jobs"); }
    },
    {
      icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
      label: "Toggle Dark/Light Theme",
      action: () => { document.getElementById("theme-toggle").click(); closeCommandPalette(); }
    }
  ];

  let filtered = staticCommands.filter(c => !q || c.label.toLowerCase().includes(q));

  // If query, also search items from DB
  let itemResults = [];
  if (q.length >= 2) {
    try {
      const res = await fetch(`/api/items?search=${encodeURIComponent(q)}`);
      const data = await res.json();
      itemResults = (data.items || []).slice(0, 5).map(i => ({
        icon: `<svg class="svg-icon svg-sm" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,
        label: `Open Session: ${i.title} (${i.type})`,
        action: () => { closeCommandPalette(); openItem(i.id); }
      }));
    } catch (err) {}
  }

  const allResults = [...filtered, ...itemResults];

  if (allResults.length === 0) {
    box.innerHTML = `<div style="padding: 12px; color: var(--text-muted); text-align: center;">No matching commands or sessions found.</div>`;
    return;
  }

  box.innerHTML = allResults.map((item, idx) => `
    <div class="command-item" id="cmd-item-${idx}">
      <span style="display: flex; align-items: center; gap: 10px;">
        <span style="display: flex; align-items: center; color: var(--accent-primary);">${item.icon || ''}</span>
        <span>${escapeHtml(item.label)}</span>
      </span>
      <span class="kbd-shortcut">Enter</span>
    </div>
  `).join("");

  allResults.forEach((item, idx) => {
    const el = document.getElementById(`cmd-item-${idx}`);
    if (el) el.onclick = item.action;
  });
}

// ============================================================================
// TOAST NOTIFICATIONS
// ============================================================================

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "toast";
  if (type === "error") toast.style.borderColor = "var(--status-error)";

  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// ============================================================================
// UTILITIES
// ============================================================================

function formatTimestamp(seconds) {
  const sec = Math.floor(seconds || 0);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(remM).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(remM).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return "0s";
  const sec = Math.floor(seconds);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatBulletPoints(text) {
  if (!text) return "";
  const lines = text.split("\n").filter(l => l.trim().length > 0);
  return `<ul style="padding-left: 20px; line-height: 1.8;">${lines.map(l => `<li>${escapeHtml(l.replace(/^[-*•\d.]\s*/, ''))}</li>`).join("")}</ul>`;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ============================================================================
// PHASE 3D: ASSISTANT COMMAND CENTER CONTROLLER
// ============================================================================

function initAssistantHome() {
  updateAssistantGreeting();
  refreshSystemSnapshot();
  loadHomeRecentIntelligence();
}

function updateAssistantGreeting() {
  const heading = document.getElementById("assistant-greeting-heading");
  if (!heading) return;
  const hour = new Date().getHours();
  let greet = "Good evening.";
  if (hour < 12) greet = "Good morning.";
  else if (hour < 17) greet = "Good afternoon.";
  heading.textContent = greet;
}

function toggleMoreTools() {
  const drawer = document.getElementById("more-tools-drawer");
  const arrow = document.getElementById("more-tools-arrow");
  if (!drawer) return;
  drawer.classList.toggle("collapsed");
  if (arrow) {
    arrow.style.transform = drawer.classList.contains("collapsed") ? "rotate(0deg)" : "rotate(180deg)";
  }
}

function setAndRunCommand(query) {
  const input = document.getElementById("assistant-command-input");
  if (input) input.value = query;
  submitAssistantCommand(query);
}

function handleAssistantMicClick() {
  showToast("Voice command input will be enabled in Phase 3E. You can type your command in the field.");
  const input = document.getElementById("assistant-command-input");
  if (input) input.focus();
}

function toggleQuickRecord() {
  toggleRecording();
  const text = document.getElementById("quick-record-btn-text");
  const sub = document.getElementById("quick-record-btn-sub");
  setTimeout(() => {
    if (state.isRecording) {
      if (text) text.textContent = "Stop Recording";
      if (sub) sub.textContent = "Click to finish and analyze";
    } else {
      if (text) text.textContent = "Record Audio";
      if (sub) sub.textContent = "Instant microphone capture";
    }
  }, 200);
}

async function refreshSystemSnapshot() {
  try {
    const res = await fetch("/api/system/status");
    if (!res.ok) return;
    const sysData = await res.json();
    
    // Query fast system status for real-time RAM/VRAM
    const toolRes = await fetch("/api/assistant/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: "What is my RAM usage?" })
    });
    const toolJson = await toolRes.json();
    const metrics = toolJson.data || {};

    const ramElem = document.getElementById("snap-ram-pct");
    const ramState = document.getElementById("snap-ram-state");
    if (ramElem && metrics.ram_percent != null) {
      ramElem.textContent = `${Math.round(metrics.ram_percent)}%`;
      if (ramState) ramState.textContent = metrics.policy_state || "Normal";
    }

    const cpuElem = document.getElementById("snap-cpu-pct");
    if (cpuElem && metrics.cpu_percent != null) {
      cpuElem.textContent = `${Math.round(metrics.cpu_percent)}%`;
    }

    const gpuElem = document.getElementById("snap-gpu-val");
    const gpuState = document.getElementById("snap-gpu-state");
    if (gpuElem && metrics.gpu_name) {
      gpuElem.textContent = metrics.gpu_name.replace("NVIDIA GeForce ", "");
      if (gpuState) gpuState.textContent = `${metrics.vram_used_mb || 0} MB VRAM`;
    }

    // Disk status
    const diskRes = await fetch("/api/assistant/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: "How much storage is left?" })
    });
    const diskJson = await diskRes.json();
    const diskMetrics = diskJson.data || {};
    const storageElem = document.getElementById("snap-storage-val");
    if (storageElem && diskMetrics.drive_free_gb != null) {
      storageElem.textContent = `${diskMetrics.drive_free_gb} GB`;
    }

    // Header assistant status pill
    const pillText = document.getElementById("assistant-system-status-text");
    if (pillText) pillText.textContent = metrics.policy_state === "NORMAL" ? "Ready" : (metrics.policy_state || "Active");

  } catch (err) {
    console.warn("System snapshot update failed:", err);
  }
}

async function loadHomeRecentIntelligence() {
  const container = document.getElementById("home-recent-intel-items");
  if (!container) return;

  try {
    const res = await fetch("/api/items?limit=4");
    const data = await res.json();
    const items = data.items || [];

    if (items.length === 0) {
      container.innerHTML = `<div style="padding: 18px; text-align: center; color: var(--text-muted); font-size: 0.88rem; background: var(--bg-surface); border: 1px dashed var(--border-subtle); border-radius: var(--radius-md);">No media sessions yet. Click 'Start Meeting' or 'Record' above to begin.</div>`;
      return;
    }

    container.innerHTML = items.map(item => `
      <div class="recent-intel-item" onclick="openItem(${item.id})">
        <div class="recent-intel-details">
          <div class="recent-intel-title">${escapeHtml(item.title)}</div>
          <div class="recent-intel-meta">
            <span class="type-badge type-${item.type || 'meeting'}">${item.type || 'meeting'}</span>
            <span>${formatDuration(item.duration)}</span>
            <span>•</span>
            <span>${item.created_at || ''}</span>
          </div>
        </div>
        <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.78rem; display: flex; align-items: center; gap: 4px;">
          <span>Open</span>
          <svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
    `).join("");

  } catch (err) {
    console.error("Failed to load recent intelligence:", err);
  }
}

function formatAssistantDataCard(d) {
  if (!d || typeof d !== "object") return "";

  // 1. Desktop Context / Active Window Card
  if (d.window_title !== undefined && d.application !== undefined) {
    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px; display: flex; justify-content: space-between;">
          <span>CURRENT ACTIVITY</span>
          <span style="color: var(--accent-primary, #6366f1); font-weight: bold;">${escapeHtml(d.application || 'Desktop')}</span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 4px; color: var(--text-muted);">
          <div>Application: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.application || 'Desktop')}</span></div>
          <div>Window: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.window_title || 'N/A')}</span></div>
          <div>Process: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.process_name || 'explorer.exe')} (PID ${d.pid || 0})</span></div>
        </div>
      </div>
    `;
  }

  // 2. Desktop Files Card
  if (d.files && Array.isArray(d.files)) {
    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px; display: flex; justify-content: space-between;">
          <span>DESKTOP FILES</span>
          <span style="color: var(--accent-primary, #6366f1);">${d.count || d.files.length} found</span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 4px;">
          ${d.files.slice(0, 5).map(f => `
            <div style="display: flex; justify-content: space-between; color: var(--text-primary); padding: 2px 0;">
              <span>📄 ${escapeHtml(f.name)}</span>
              <span style="color: var(--text-muted); font-size: 0.8rem;">${f.size_kb} KB • ${escapeHtml(f.directory || '')}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
  }

  // 3. System Status Card
  if (d.ram_percent !== undefined && d.cpu_percent !== undefined) {
    const statusColor = d.overall_status === 'HEALTHY' ? 'var(--status-success, #10b981)' : (d.overall_status === 'CAUTION' ? 'var(--status-warning, #f59e0b)' : 'var(--status-error, #ef4444)');
    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px; display: flex; justify-content: space-between;">
          <span>SYSTEM HEALTH</span>
          <span style="color: ${statusColor}; font-weight: bold;">${d.overall_status || 'HEALTHY'}</span>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; color: var(--text-muted);">
          <div>RAM: <span style="color: var(--text-primary); font-weight: 500;">${d.ram_percent}%</span> (${d.ram_used_mb || 0}/${d.ram_total_mb || 0} MB)</div>
          <div>CPU: <span style="color: var(--text-primary); font-weight: 500;">${d.cpu_percent}%</span> (${d.cpu_cores || 4} cores)</div>
          <div>GPU: <span style="color: var(--text-primary); font-weight: 500;">${d.gpu_utilization || 0}%</span> (${d.gpu_name || 'N/A'})</div>
          <div>VRAM: <span style="color: var(--text-primary); font-weight: 500;">${d.vram_used_mb || 0} / ${d.vram_total_mb || 0} MB</span></div>
          <div>Storage: <span style="color: var(--text-primary); font-weight: 500;">${d.storage_free_gb || 'N/A'} GB free</span></div>
          <div>AI Tasks: <span style="color: var(--text-primary); font-weight: 500;">${d.active_jobs || 0} active</span></div>
        </div>
      </div>
    `;
  }

  // 4. Top Resource Consumers Card
  if (d.top_ram_processes && d.top_ram_processes.length > 0) {
    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px;">TOP RESOURCE CONSUMERS</div>
        <div style="margin-bottom: 6px; color: var(--text-muted); font-size: 0.8rem;">Top Memory:</div>
        <div style="display: flex; flex-direction: column; gap: 3px; margin-bottom: 8px;">
          ${d.top_ram_processes.slice(0, 3).map(p => `<div style="display: flex; justify-content: space-between; color: var(--text-primary);"><span>• ${escapeHtml(p.name)} (PID ${p.pid})</span><span style="color: var(--text-muted);">${p.ram_mb} MB</span></div>`).join('')}
        </div>
        <div style="margin-bottom: 6px; color: var(--text-muted); font-size: 0.8rem;">Top CPU:</div>
        <div style="display: flex; flex-direction: column; gap: 3px;">
          ${(d.top_cpu_processes || []).slice(0, 3).map(p => `<div style="display: flex; justify-content: space-between; color: var(--text-primary);"><span>• ${escapeHtml(p.name)} (PID ${p.pid})</span><span style="color: var(--text-muted);">${p.cpu_percent}%</span></div>`).join('')}
        </div>
      </div>
    `;
  }

  // 5. Note Created Card
  if (d.file_name && d.note_title !== undefined) {
    const verifiedBadge = d.verified ? 
      `<span style="color: var(--status-success, #10b981); font-weight: bold;">✓ Verified (${d.size_bytes || 0} bytes on disk)</span>` : 
      `<span style="color: var(--status-warning, #f59e0b); font-weight: bold;">⚠ Unverified</span>`;
    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px; display: flex; justify-content: space-between;">
          <span>NOTE CREATED</span>
          ${verifiedBadge}
        </div>
        <div style="display: flex; flex-direction: column; gap: 4px; color: var(--text-muted);">
          <div>Title: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.note_title)}</span></div>
          <div>File: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.file_name)}</span></div>
          <div>Location: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.path || '')}</span></div>
        </div>
      </div>
    `;
  }

  // 6. Folder Created Card
  if (d.folder_name && d.parent !== undefined) {
    const verifiedBadge = d.verified ? 
      `<span style="color: var(--status-success, #10b981); font-weight: bold;">✓ Verified (Directory Created)</span>` : 
      `<span style="color: var(--status-warning, #f59e0b); font-weight: bold;">⚠ Unverified</span>`;
    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px; display: flex; justify-content: space-between;">
          <span>FOLDER CREATED</span>
          ${verifiedBadge}
        </div>
        <div style="display: flex; flex-direction: column; gap: 4px; color: var(--text-muted);">
          <div>Folder: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.folder_name)}</span></div>
          <div>Parent: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.parent)}</span></div>
          <div>Location: <span style="color: var(--text-primary); font-weight: 500;">${escapeHtml(d.path || '')}</span></div>
        </div>
      </div>
    `;
  }

  // 7. Multi-step Task Plan Card
  const plan = d.plan || (d.steps ? d : null);
  if (plan && plan.steps && Array.isArray(plan.steps)) {
    const statusBadges = {
      succeeded: '<span style="color: var(--status-success, #10b981); font-weight: bold;">✓ Succeeded</span>',
      executing: '<span style="color: var(--accent-primary, #6366f1); font-weight: bold;">⚙ Executing</span>',
      awaiting_approval: '<span style="color: var(--status-warning, #f59e0b); font-weight: bold;">⚠ Awaiting Approval</span>',
      failed: '<span style="color: var(--status-error, #ef4444); font-weight: bold;">✕ Failed</span>',
      cancelled: '<span style="color: var(--text-muted); font-weight: bold;">⊘ Cancelled</span>',
      skipped: '<span style="color: var(--text-muted); font-style: italic;">↷ Skipped</span>',
      pending: '<span style="color: var(--text-muted);">○ Pending</span>'
    };
    const planBadge = statusBadges[plan.status] || `<span style="font-weight: bold;">${escapeHtml(plan.status)}</span>`;

    const contextUsedHtml = (plan.context_used && plan.context_used.length > 0) ? `
      <div style="margin-bottom: 8px; padding: 6px 10px; background: rgba(16, 185, 129, 0.08); border-radius: 4px; border: 1px solid rgba(16, 185, 129, 0.2);">
        <div style="font-size: 0.75rem; font-weight: 600; color: var(--status-success, #10b981); margin-bottom: 4px;">CONTEXT USED:</div>
        <div style="display: flex; flex-direction: column; gap: 2px;">
          ${plan.context_used.map(cu => `
            <div style="font-size: 0.75rem; color: var(--text-secondary); display: flex; align-items: center; gap: 4px;">
              <span style="color: var(--status-success, #10b981);">✓</span> ${escapeHtml(cu)}
            </div>
          `).join('')}
        </div>
      </div>
    ` : '';

    const adaptationHtml = plan.is_adapted ? `
      <div style="font-size: 0.75rem; color: var(--accent-primary, #6366f1); margin-bottom: 6px; font-style: italic;">
        ⚡ Plan adapted: ${escapeHtml(plan.adaptation_reason || 'Context-aware adaptation applied')}
      </div>
    ` : '';

    return `
      <div style="margin-top: 10px; padding: 12px; border-radius: 8px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color); font-family: monospace; font-size: 0.85rem;">
        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
          <span style="display: flex; align-items: center; gap: 6px;">
            <svg class="svg-icon svg-xs" viewBox="0 0 24 24" style="color: var(--accent-primary, #6366f1);"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>
            <span>TASK PLAN: ${escapeHtml(plan.goal || 'Execution Plan')}</span>
          </span>
          ${planBadge}
        </div>
        ${contextUsedHtml}
        ${adaptationHtml}
        <div style="font-size: 0.75rem; font-weight: 600; color: var(--text-muted); margin-bottom: 4px;">PLAN:</div>
        <div style="display: flex; flex-direction: column; gap: 6px; margin-top: 2px;">
          ${plan.steps.map((s, idx) => {
            const stepBadge = statusBadges[s.status] || `<span>${s.status}</span>`;
            const duration = s.duration_ms ? ` (${s.duration_ms}ms)` : '';
            const stepTitle = s.reused ? `Reuse ${escapeHtml(s.description || s.tool)} ✓` : escapeHtml(s.description || s.tool);
            return `
              <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: rgba(0,0,0,0.2); border-radius: 4px; border: 1px solid rgba(255,255,255,0.05);">
                <span><strong>${idx + 1}. ${stepTitle}</strong><span style="color: var(--text-muted);">${duration}</span></span>
                ${stepBadge}
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  return "";
}

async function submitAssistantCommand(explicitQuery = null) {
  const input = document.getElementById("assistant-command-input");
  const query = (explicitQuery || (input ? input.value : "")).trim();
  if (!query) return;

  if (input) input.value = "";

  const box = document.getElementById("assistant-dialogue-box");
  if (box) box.style.display = "flex";

  // Task Card update
  const taskHead = document.getElementById("active-task-heading");
  const taskDesc = document.getElementById("active-task-desc");
  const taskBadge = document.getElementById("active-task-badge");
  if (taskHead) taskHead.textContent = "Processing Request...";
  if (taskDesc) taskDesc.textContent = `Analyzing: "${query}"`;
  if (taskBadge) {
    taskBadge.textContent = "● Thinking";
    taskBadge.className = "type-badge type-lecture";
  }

  // Add User turn to dialogue
  const turnId = Date.now();
  const userHtml = `
    <div class="dialogue-turn-user">
      <div>${escapeHtml(query)}</div>
    </div>
  `;

  // Placeholder for Assistant response with Tool Lifecycle
  const isBrowserQuery = /\b(page|article|webpage|reading|what is on this page)\b/i.test(query);
  const initialBadge = isBrowserQuery ? "READING PAGE..." : "UNDERSTANDING";
  const initialLabel = isBrowserQuery ? "Reading page & extracting content..." : "Analyzing intent and routing...";

  const assistantHtml = `
    <div class="dialogue-turn-assistant" id="turn-assistant-${turnId}">
      <div class="tool-activity-card" id="tool-card-${turnId}">
        <span class="tool-state-badge tool-state-listening" id="tool-badge-${turnId}">
          <svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 14 14"/></svg>
          <span>${initialBadge}</span>
        </span>
        <span id="tool-label-${turnId}">${initialLabel}</span>
      </div>
      <div class="assistant-content-text" id="assistant-text-${turnId}" style="color: var(--text-muted); font-style: italic;">
        Thinking...
      </div>
    </div>
  `;

  if (box) {
    box.innerHTML += userHtml + assistantHtml;
    box.scrollTop = box.scrollHeight;
  }

  try {
    const res = await fetch("/api/assistant/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: query,
        session_id: state.activeItemId
      })
    });

    const data = await res.json();

    const toolBadge = document.getElementById(`tool-badge-${turnId}`);
    const toolLabel = document.getElementById(`tool-label-${turnId}`);
    const textElem = document.getElementById(`assistant-text-${turnId}`);

    if (taskHead) taskHead.textContent = "SmartScribe Assistant";
    if (taskDesc) taskDesc.textContent = "Idle • Local Qwen2.5-3B and Faster-Whisper ready";
    if (taskBadge) {
      taskBadge.textContent = "● Standby";
      taskBadge.className = "type-badge type-meeting";
    }

    if (!data.success && (data.status === "dynamic_page_requires_extension" || (data.message && data.message.includes("browser extension")))) {
      // Dynamic page requires extension
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-waiting";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>EXTENSION REQUIRED</span>`;
      }
      if (toolLabel) toolLabel.textContent = "This page requires the SmartScribe browser extension.";
      if (textElem) {
        textElem.style.fontStyle = "normal";
        textElem.style.color = "var(--text-primary)";
        textElem.innerHTML = `<strong>Notice:</strong> This page requires the SmartScribe browser extension.`;
      }
      return;
    }

    if (!data.success && data.status === "waiting_for_confirmation") {
      // High-risk action requiring confirmation
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-waiting";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>WAITING FOR CONFIRMATION</span>`;
      }
      if (toolLabel) toolLabel.textContent = `Action '${data.tool_name || 'operation'}' requires explicit user approval.`;
      if (textElem) {
        textElem.style.fontStyle = "normal";
        textElem.style.color = "var(--text-primary)";
        textElem.innerHTML = `<strong>Confirmation Required:</strong> Please review and approve the requested action in the modal.`;
      }
      showConfirmationModal(data, turnId);
      return;
    }

    if (!data.success && data.status === "verification_failed") {
      // Action verification failure
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-failed";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>VERIFICATION FAILED</span>`;
      }
      if (toolLabel) toolLabel.textContent = `Action executed, but verification failed: ${data.message || 'System state could not be verified'}`;
      if (textElem) {
        textElem.style.fontStyle = "normal";
        textElem.style.color = "var(--status-error)";
        textElem.textContent = data.response || data.message || "Action was performed, but verification failed.";
      }
      return;
    }

    if (!data.success) {
      // Error or blocked
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-failed";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg><span>${data.status === 'blocked' ? 'SECURITY ALERT' : 'FAILED'}</span>`;
      }
      if (toolLabel) toolLabel.textContent = data.message || "Request could not be processed.";
      if (textElem) {
        textElem.style.fontStyle = "normal";
        textElem.style.color = "var(--status-error)";
        textElem.textContent = data.response || data.message || "An error occurred.";
      }
      return;
    }

    // Success response
    if (data.path === "plan_path" || data.plan) {
      if (toolBadge) {
        const isAllOk = data.status === "succeeded" || data.status === "complete";
        toolBadge.className = isAllOk ? "tool-state-badge tool-state-complete" : (data.status === "partial_success" ? "tool-state-badge tool-state-waiting" : "tool-state-badge tool-state-failed");
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg><span>${data.status ? data.status.toUpperCase() : 'PLAN'}</span>`;
      }
      if (toolLabel) toolLabel.textContent = `Task Plan: ${data.plan ? data.plan.goal : 'Multi-Step Execution'} (${data.duration_ms ? data.duration_ms + 'ms' : 'complete'})`;
    } else if (data.tool_name && (data.tool_name.includes("page") || data.tool_name.includes("browser"))) {
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-complete";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><span>COMPLETE</span>`;
      }
      const pageTitle = (data.data && data.data.title) ? data.data.title : "Webpage Article";
      if (toolLabel) toolLabel.textContent = `Article: ${pageTitle} (${data.duration_ms ? (data.duration_ms/1000).toFixed(1) + 's' : 'complete'})`;
    } else if (data.path === "fast_path" || data.path === "hybrid_path") {
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-complete";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><span>COMPLETE</span>`;
      }
      const toolName = data.tool_name || data.intent || "tool";
      if (toolLabel) toolLabel.textContent = `Executed: ${toolName} (${data.duration_ms ? data.duration_ms + 'ms' : 'fast path'})`;
    } else {
      // AI Path
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-complete";
        toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3z"/></svg><span>AI REASONING</span>`;
      }
      if (toolLabel) toolLabel.textContent = `Qwen2.5-3B Local LLM (${data.duration_ms ? (data.duration_ms/1000).toFixed(1) + 's' : 'complete'})`;
    }

    if (textElem) {
      textElem.style.fontStyle = "normal";
      textElem.style.color = "var(--text-primary)";

      let htmlContent = escapeHtml(data.response).replace(/\n/g, "<br>");

      if (data.data) {
        htmlContent += formatAssistantDataCard(data.data);
      }
      if (data.plan) {
        htmlContent += formatAssistantDataCard({ plan: data.plan });
      }

      textElem.innerHTML = htmlContent;
    }

    if (data.spoken_text) {
      setAssistantSpeakingUI(true);
      pollSpeechStatus();
    }

    // Refresh snapshots if relevant
    if (data.intent === "get_system_status" || data.intent === "get_disk_status" || data.intent === "get_storage_status" || data.intent === "analyze_system_status" || data.intent === "get_process_summary" || data.intent === "diagnose_smartscribe") {
      refreshSystemSnapshot();
    }

  } catch (err) {
    console.error("Assistant command failed:", err);
    const textElem = document.getElementById(`assistant-text-${turnId}`);
    if (textElem) {
      textElem.style.color = "var(--status-error)";
      textElem.textContent = `Failed to connect to local SmartScribe AI server: ${err.message}`;
    }
  }
}

// Confirmation Protocol
let pendingConfirmationTurnId = null;

function showConfirmationModal(reqData, turnId = null) {
  pendingConfirmationTurnId = turnId;
  const modal = document.getElementById("assistant-confirmation-modal");
  const prompt = document.getElementById("confirm-modal-prompt");
  const argsBox = document.getElementById("confirm-modal-args");
  const tokenInput = document.getElementById("confirm-active-token");

  if (!modal) return;

  if (prompt) prompt.textContent = reqData.confirmation_prompt || reqData.message || "Please confirm this action.";
  if (argsBox) argsBox.textContent = JSON.stringify(reqData.arguments || {}, null, 2);
  if (tokenInput) tokenInput.value = reqData.confirmation_token || "";

  modal.style.display = "flex";
}

async function handleConfirmApprove() {
  const tokenInput = document.getElementById("confirm-active-token");
  const token = tokenInput ? tokenInput.value : "";
  const modal = document.getElementById("assistant-confirmation-modal");
  if (modal) modal.style.display = "none";

  if (!token) return;

  try {
    const res = await fetch("/api/assistant/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirmation_token: token,
        action: "approve"
      })
    });

    const data = await res.json();
    if (data.success) {
      showToast("Action confirmed and executed successfully.");
      if (pendingConfirmationTurnId) {
        const textElem = document.getElementById(`assistant-text-${pendingConfirmationTurnId}`);
        const toolBadge = document.getElementById(`tool-badge-${pendingConfirmationTurnId}`);
        if (toolBadge) {
          toolBadge.className = "tool-state-badge tool-state-complete";
          toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><span>COMPLETE</span>`;
        }
        if (textElem) {
          textElem.innerHTML = `<strong>Action Confirmed:</strong> ${escapeHtml(data.message || 'Executed successfully.')}`;
        }
      }
    } else {
      showToast(`Confirmation failed: ${data.message}`, "error");
    }
  } catch (err) {
    showToast(`Error approving action: ${err.message}`, "error");
  }
}

async function handleConfirmDeny() {
  const tokenInput = document.getElementById("confirm-active-token");
  const token = tokenInput ? tokenInput.value : "";
  const modal = document.getElementById("assistant-confirmation-modal");
  if (modal) modal.style.display = "none";

  if (!token) return;

  try {
    await fetch("/api/assistant/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirmation_token: token,
        action: "deny"
      })
    });
    showToast("Action cancelled by user.");
    if (pendingConfirmationTurnId) {
      const textElem = document.getElementById(`assistant-text-${pendingConfirmationTurnId}`);
      const toolBadge = document.getElementById(`tool-badge-${pendingConfirmationTurnId}`);
      if (toolBadge) {
        toolBadge.className = "tool-state-badge tool-state-failed";
        toolBadge.innerHTML = `<span>CANCELLED</span>`;
      }
      if (textElem) {
        textElem.innerHTML = `<em>Action cancelled by user.</em>`;
      }
    }
  } catch (err) {
    console.error("Error denying confirmation:", err);
  }
}

// =============================================================
// PHASE 3E: VOICE ASSISTANT & PUSH-TO-TALK CONTROLLER
// =============================================================

let isVoiceListening = false;
let voiceMediaRecorder = null;
let voiceAudioChunks = [];
let voiceStream = null;
let pttKeyDownActive = false;
let currentVoiceTurnId = null;
let isUsingBrowserVoiceCapture = true;

async function startVoiceInput() {
  if (isVoiceListening) return;

  // Instant barge-in: stop any active speech response before opening microphone
  stopAssistantSpeech().catch(() => {});

  const micBtn = document.getElementById("assistant-mic-btn");
  if (micBtn) {
    micBtn.classList.add("listening");
    micBtn.title = "Listening... Release Alt+V or click to stop";
  }

  isVoiceListening = true;
  currentVoiceTurnId = Date.now();

  const box = document.getElementById("assistant-dialogue-box");
  if (box) box.style.display = "flex";

  const history = document.getElementById("assistant-dialogue-history");
  if (history) {
    const turnHtml = `
      <div class="dialogue-turn-user" id="user-turn-${currentVoiceTurnId}">
        <span class="dialogue-voice-tag"><svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg> Voice</span>
        <div id="user-text-${currentVoiceTurnId}"><em>Listening...</em></div>
      </div>
      <div class="dialogue-turn-assistant" id="turn-assistant-${currentVoiceTurnId}">
        <div class="tool-activity-card" id="tool-card-${currentVoiceTurnId}">
          <span class="tool-state-badge tool-state-listening" id="tool-badge-${currentVoiceTurnId}">
            <svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
            <span>LISTENING</span>
          </span>
          <div class="assistant-response-content" id="assistant-text-${currentVoiceTurnId}">
            <div class="assistant-loading-pulse">
              <span class="pulse-dot"></span><span class="pulse-dot"></span><span class="pulse-dot"></span>
            </div>
          </div>
        </div>
      </div>
    `;
    history.insertAdjacentHTML("beforeend", turnHtml);
    history.scrollTop = history.scrollHeight;
  }

  try {
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      isUsingBrowserVoiceCapture = true;
      voiceAudioChunks = [];
      voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceMediaRecorder = new MediaRecorder(voiceStream);
      voiceMediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          voiceAudioChunks.push(e.data);
        }
      };
      voiceMediaRecorder.start(100);
    } else {
      isUsingBrowserVoiceCapture = false;
      await fetch("/api/assistant/voice/start", { method: "POST" });
    }
  } catch (err) {
    console.warn("Microphone access failed, falling back to backend capture:", err);
    try {
      isUsingBrowserVoiceCapture = false;
      await fetch("/api/assistant/voice/start", { method: "POST" });
    } catch (backendErr) {
      console.error("Voice capture failed:", backendErr);
      showToast(`Microphone error: ${err.message || backendErr.message}`, "error");
      cancelVoiceInput();
    }
  }
}

async function stopVoiceInput() {
  if (!isVoiceListening) return;
  isVoiceListening = false;
  pttKeyDownActive = false;

  const micBtn = document.getElementById("assistant-mic-btn");
  if (micBtn) {
    micBtn.classList.remove("listening");
    micBtn.title = "Push-to-talk (Alt+V or Click)";
  }

  const turnId = currentVoiceTurnId;
  const toolBadge = document.getElementById(`tool-badge-${turnId}`);
  if (toolBadge) {
    toolBadge.className = "tool-state-badge transcribing";
    toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/></svg><span>TRANSCRIBING</span>`;
  }

  if (isUsingBrowserVoiceCapture && voiceMediaRecorder) {
    voiceMediaRecorder.onstop = async () => {
      if (voiceStream) {
        voiceStream.getTracks().forEach(track => track.stop());
        voiceStream = null;
      }

      const mimeType = voiceMediaRecorder.mimeType || "audio/webm";
      const audioBlob = new Blob(voiceAudioChunks, { type: mimeType });
      voiceAudioChunks = [];

      if (audioBlob.size < 500) {
        handleVoiceError(turnId, "No speech detected or audio was too short.");
        return;
      }

      await dispatchVoiceAudio(audioBlob, turnId);
    };

    try {
      if (voiceMediaRecorder.state !== "inactive") {
        voiceMediaRecorder.stop();
      }
    } catch (err) {
      console.error("Error stopping MediaRecorder:", err);
    }
  } else {
    // Backend VoiceCapture stop
    try {
      const res = await fetch("/api/assistant/voice/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const data = await res.json();
      handleVoiceAssistantResult(turnId, data);
    } catch (err) {
      handleVoiceError(turnId, `Voice processing error: ${err.message}`);
    }
  }
}

async function dispatchVoiceAudio(audioBlob, turnId) {
  const toolBadge = document.getElementById(`tool-badge-${turnId}`);
  if (toolBadge) {
    toolBadge.className = "tool-state-badge understanding";
    toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg><span>UNDERSTANDING</span>`;
  }

  const formData = new FormData();
  formData.append("audio", audioBlob, "voice_command.webm");

  try {
    const res = await fetch("/api/assistant/voice", {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    handleVoiceAssistantResult(turnId, data);
  } catch (err) {
    handleVoiceError(turnId, `Failed to contact AI server: ${err.message}`);
  }
}

function handleVoiceAssistantResult(turnId, data) {
  const userText = document.getElementById(`user-text-${turnId}`);
  const toolBadge = document.getElementById(`tool-badge-${turnId}`);
  const textElem = document.getElementById(`assistant-text-${turnId}`);

  if (userText && data.recognized_text) {
    userText.textContent = `"${data.recognized_text}"`;
  } else if (userText && !data.recognized_text) {
    userText.innerHTML = `<em>(Unrecognized Audio)</em>`;
  }

  if (data.status === "complete") {
    if (toolBadge) {
      toolBadge.className = "tool-state-badge tool-state-complete";
      toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg><span>COMPLETE</span>`;
    }
    if (textElem) {
      let htmlContent = escapeHtml(data.response || "Executed successfully.").replace(/\n/g, "<br>");
      if (data.data) {
        htmlContent += formatAssistantDataCard(data.data);
      }
      if (data.plan) {
        htmlContent += formatAssistantDataCard({ plan: data.plan });
      }
      textElem.innerHTML = htmlContent;
    }
    if (data.spoken_text) {
      setAssistantSpeakingUI(true);
      pollSpeechStatus();
    }
  } else if (data.status === "confirmation_required") {
    if (toolBadge) {
      toolBadge.className = "tool-state-badge tool-state-waiting";
      toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><span>CONFIRMATION REQUIRED</span>`;
    }
    if (textElem) {
      textElem.innerHTML = `<strong>Action requires approval:</strong> ${escapeHtml(data.response || data.message)}`;
    }
    showConfirmationModal(data, turnId);
  } else {
    if (toolBadge) {
      toolBadge.className = "tool-state-badge tool-state-failed";
      toolBadge.innerHTML = `<svg class="svg-icon svg-xs" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg><span>FAILED</span>`;
    }
    if (textElem) {
      textElem.textContent = data.response || data.message || "Speech command could not be processed.";
    }
  }

  if (data.intent === "get_system_status" || data.intent === "get_disk_status" || data.intent === "analyze_system_status") {
    refreshSystemSnapshot();
  }
}

function handleVoiceError(turnId, message) {
  const toolBadge = document.getElementById(`tool-badge-${turnId}`);
  const textElem = document.getElementById(`assistant-text-${turnId}`);
  if (toolBadge) {
    toolBadge.className = "tool-state-badge tool-state-failed";
    toolBadge.innerHTML = `<span>FAILED</span>`;
  }
  if (textElem) {
    textElem.textContent = message;
  }
}

function cancelVoiceInput() {
  if (!isVoiceListening) return;
  isVoiceListening = false;
  pttKeyDownActive = false;

  const micBtn = document.getElementById("assistant-mic-btn");
  if (micBtn) {
    micBtn.classList.remove("listening");
    micBtn.title = "Push-to-talk (Alt+V or Click)";
  }

  if (voiceMediaRecorder && voiceMediaRecorder.state !== "inactive") {
    try { voiceMediaRecorder.stop(); } catch (e) {}
  }
  if (voiceStream) {
    try { voiceStream.getTracks().forEach(t => t.stop()); } catch (e) {}
    voiceStream = null;
  }
  voiceAudioChunks = [];

  fetch("/api/assistant/voice/cancel", { method: "POST" }).catch(() => {});

  if (currentVoiceTurnId) {
    const toolBadge = document.getElementById(`tool-badge-${currentVoiceTurnId}`);
    const textElem = document.getElementById(`assistant-text-${currentVoiceTurnId}`);
    if (toolBadge) {
      toolBadge.className = "tool-state-badge tool-state-failed";
      toolBadge.innerHTML = `<span>CANCELLED</span>`;
    }
    if (textElem) {
      textElem.innerHTML = `<em>Voice input cancelled.</em>`;
    }
  }
  showToast("Voice input cancelled.");
}

function handleAssistantMicClick() {
  if (isVoiceListening) {
    stopVoiceInput();
  } else {
    startVoiceInput();
  }
}

// Push-to-Talk Hotkey Listener (Alt+V and Escape)
window.addEventListener("keydown", (e) => {
  if (e.altKey && (e.key === "v" || e.key === "V") && !e.repeat) {
    e.preventDefault();
    if (!isVoiceListening) {
      pttKeyDownActive = true;
      startVoiceInput();
    }
  } else if (e.key === "Escape") {
    if (isVoiceListening) {
      e.preventDefault();
      cancelVoiceInput();
    } else if (isAssistantSpeaking) {
      e.preventDefault();
      stopAssistantSpeech();
    }
  }
});

window.addEventListener("keyup", (e) => {
  if ((e.key === "v" || e.key === "V") && pttKeyDownActive) {
    e.preventDefault();
    pttKeyDownActive = false;
    stopVoiceInput();
  }
});

// =============================================================
// PHASE 3I: LOCAL VOICE RESPONSE / SPEECH CONTROLLER
// =============================================================

let isAssistantSpeaking = false;
let speechPollInterval = null;

async function stopAssistantSpeech() {
  try {
    const res = await fetch("/api/assistant/speech/stop", { method: "POST" });
    const data = await res.json();
    setAssistantSpeakingUI(false);
    return data;
  } catch (err) {
    console.error("Failed to stop assistant speech:", err);
    setAssistantSpeakingUI(false);
  }
}

function setAssistantSpeakingUI(speaking) {
  isAssistantSpeaking = speaking;
  const pill = document.getElementById("assistant-speech-indicator");
  const stopBtn = document.getElementById("assistant-speech-stop-btn");
  if (pill) {
    pill.style.display = speaking ? "inline-flex" : "none";
  }
  if (stopBtn) {
    stopBtn.style.display = speaking ? "inline-flex" : "none";
  }
}

function pollSpeechStatus() {
  if (speechPollInterval) clearInterval(speechPollInterval);
  speechPollInterval = setInterval(async () => {
    try {
      const res = await fetch("/api/assistant/speech/status");
      if (res.ok) {
        const data = await res.json();
        setAssistantSpeakingUI(!!data.speaking);
        if (!data.speaking && speechPollInterval) {
          clearInterval(speechPollInterval);
          speechPollInterval = null;
        }
      }
    } catch (e) {
      if (speechPollInterval) {
        clearInterval(speechPollInterval);
        speechPollInterval = null;
      }
    }
  }, 250);
}
