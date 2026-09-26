interface VocabItem {
  infinitive?: string;
  phrasal_verb?: string;
  native_language_definition: string;
  from_source: string[];
  example_sentence: string;
}

interface SocketMessage {
  status: "Queued" | "Parsing" | "Phase 1: Extracting Words" | "Phase 2: Translating in Context" | "Ready" | "Failed" | "Stopped";
  detail: string;
  items?: VocabItem[];
  error?: string;
}

interface TaskDetails {
  id: string;
  status: string;
  detail: string;
  error?: string | null;
  target_language?: string;
  native_language?: string;
  cefr_level?: string;
  source?: string;
  items?: VocabItem[];
}

document.addEventListener("DOMContentLoaded", () => {
  // Extract opId from either /operation/:id or /operation.html?id=:id
  const urlParams = new URLSearchParams(window.location.search);
  let opId = urlParams.get("id");
  if (!opId) {
    const pathParts = window.location.pathname.split("/").filter(Boolean);
    const last = pathParts[pathParts.length - 1];
    if (last && !last.includes(".html")) {
      opId = last;
    }
  }

  if (!opId) {
    alert("No operation ID specified.");
    window.location.href = "/operations";
    return;
  }

  // DOM Elements
  const statusBadge = document.getElementById("status-badge") as HTMLElement;
  const statusDetail = document.getElementById("status-detail") as HTMLElement;
  const progressBar = document.getElementById("progress-bar") as HTMLProgressElement;
  const progressSpinner = document.getElementById("progress-spinner") as HTMLElement;
  const loadingContainer = document.getElementById("loading-container") as HTMLElement;
  const resultsContainer = document.getElementById("results-container") as HTMLElement;
  const wordListContainer = document.getElementById("word-list") as HTMLElement;
  const selectAllBtn = document.getElementById("select-all-btn") as HTMLButtonElement;
  const deselectAllBtn = document.getElementById("deselect-all-btn") as HTMLButtonElement;
  const exportJsonBtn = document.getElementById("export-json-btn") as HTMLButtonElement;
  const exportCsvBtn = document.getElementById("export-csv-btn") as HTMLButtonElement | null;
  const exportAnkiBtn = document.getElementById("export-anki-btn") as HTMLButtonElement;
  const stopOpBtn = document.getElementById("stop-op-btn") as HTMLButtonElement | null;
  const selectedCountSpan = document.getElementById("selected-count") as HTMLElement;

  // Metadata Display Elements
  const opIdBadge = document.getElementById("op-id-badge") as HTMLElement;
  const paramSourceLang = document.getElementById("param-source-lang") as HTMLElement;
  const paramNativeLang = document.getElementById("param-native-lang") as HTMLElement;
  const paramCefrLevel = document.getElementById("param-cefr-level") as HTMLElement;
  const paramInputSource = document.getElementById("param-input-source") as HTMLElement;

  // Steps
  const stepQueued = document.getElementById("step-queued") as HTMLElement;
  const stepParsing = document.getElementById("step-parsing") as HTMLElement;
  const stepPhase1 = document.getElementById("step-phase1") as HTMLElement;
  const stepPhase2 = document.getElementById("step-phase2") as HTMLElement;
  const stepReady = document.getElementById("step-ready") as HTMLElement;

  if (opIdBadge) opIdBadge.innerText = opId;

  let vocabItems: VocabItem[] = [];

  function setStepActive(stepElement: HTMLElement | null, isPrimary: boolean, isError = false) {
    if (!stepElement) return;
    if (isError) {
      stepElement.classList.add("step-error");
      stepElement.classList.remove("step-primary");
    } else if (isPrimary) {
      stepElement.classList.add("step-primary");
      stepElement.classList.remove("step-error");
    } else {
      stepElement.classList.remove("step-primary", "step-error");
    }
  }

  function updateStepsTracker(status: string) {
    // Reset
    [stepQueued, stepParsing, stepPhase1, stepPhase2, stepReady].forEach((el) => {
      if (el) el.classList.remove("step-primary", "step-error");
    });

    setStepActive(stepQueued, true);

    if (status === "Queued") {
      // only queued active
    } else if (status === "Parsing") {
      setStepActive(stepParsing, true);
    } else if (status.includes("Phase 1")) {
      setStepActive(stepParsing, true);
      setStepActive(stepPhase1, true);
    } else if (status.includes("Phase 2")) {
      setStepActive(stepParsing, true);
      setStepActive(stepPhase1, true);
      setStepActive(stepPhase2, true);
    } else if (status === "Ready") {
      setStepActive(stepParsing, true);
      setStepActive(stepPhase1, true);
      setStepActive(stepPhase2, true);
      setStepActive(stepReady, true);
    } else if (status === "Failed") {
      setStepActive(stepReady, false, true);
    } else if (status === "Stopped") {
      // keep current active steps, stop spinner
    }
  }

  function updateStatus(status: string, detail: string) {
    if (statusBadge) statusBadge.innerText = status;
    if (statusDetail) statusDetail.innerText = detail;

    updateStepsTracker(status);

    if (progressBar) {
      if (status === "Queued") {
        progressBar.value = 10;
      } else if (status === "Parsing") {
        progressBar.value = 25;
      } else if (status.includes("Phase 1")) {
        progressBar.value = 55;
      } else if (status.includes("Phase 2")) {
        progressBar.value = 85;
      } else if (status === "Ready") {
        progressBar.value = 100;
        if (stopOpBtn) stopOpBtn.classList.add("hidden");
        if (progressSpinner) progressSpinner.classList.add("hidden");
      } else if (status === "Failed") {
        progressBar.classList.add("progress-error");
        if (stopOpBtn) stopOpBtn.classList.add("hidden");
        if (progressSpinner) progressSpinner.classList.add("hidden");
      } else if (status === "Stopped") {
        progressBar.classList.add("progress-warning");
        if (progressSpinner) progressSpinner.classList.add("hidden");
        if (stopOpBtn) {
          stopOpBtn.disabled = true;
          stopOpBtn.innerText = "Stopped";
        }
      }
    }
  }

  function setMetadata(data: TaskDetails) {
    if (paramSourceLang && data.target_language) paramSourceLang.innerText = data.target_language;
    if (paramNativeLang && data.native_language) paramNativeLang.innerText = data.native_language;
    if (paramCefrLevel && data.cefr_level) paramCefrLevel.innerText = data.cefr_level;
    if (paramInputSource && data.source) {
      paramInputSource.innerText = data.source;
      paramInputSource.title = data.source;
    }
  }

  if (stopOpBtn) {
    stopOpBtn.addEventListener("click", async () => {
      stopOpBtn.disabled = true;
      stopOpBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Stopping...';
      try {
        const res = await fetch(`/api/tasks/${opId}/stop`, { method: "POST" });
        if (!res.ok) throw new Error("Failed to stop operation");
      } catch (err: any) {
        alert(`Could not stop operation: ${err.message}`);
        stopOpBtn.disabled = false;
        stopOpBtn.innerText = "Stop Operation";
      }
    });
  }

  function renderWordList(items: VocabItem[]) {
    vocabItems = items;
    if (loadingContainer) loadingContainer.classList.add("hidden");
    if (resultsContainer) resultsContainer.classList.remove("hidden");

    if (!wordListContainer) return;
    wordListContainer.innerHTML = "";

    if (items.length === 0) {
      wordListContainer.innerHTML = `
        <div class="alert alert-info">
          <span>No words found matching this CEFR level threshold in the provided source.</span>
        </div>
      `;
      return;
    }

    items.forEach((item, index) => {
      const term = item.infinitive || item.phrasal_verb || "Unknown";
      const isPhrasal = !!item.phrasal_verb;
      const variants = item.from_source ? item.from_source.join(", ") : term;

      const card = document.createElement("div");
      card.className = "card bg-base-100 border border-base-300 shadow-sm hover:border-primary transition-all p-4";

      card.innerHTML = `
        <div class="flex items-start gap-4">
          <input type="checkbox" id="word-chk-${index}" data-index="${index}" class="checkbox checkbox-primary mt-1 word-checkbox" checked />
          <div class="flex-1">
            <div class="flex items-center gap-2">
              <label for="word-chk-${index}" class="font-bold text-lg cursor-pointer">${term}</label>
              ${isPhrasal ? '<span class="badge badge-secondary badge-xs">phrasal verb</span>' : ''}
              <span class="badge badge-ghost badge-xs opacity-75">forms: ${variants}</span>
            </div>
            <p class="text-sm text-primary font-medium mt-1">${item.native_language_definition}</p>
            <div class="mt-2 text-xs bg-base-200 p-2 rounded text-base-content/80 italic">
              "${item.example_sentence}"
            </div>
          </div>
        </div>
      `;
      wordListContainer.appendChild(card);
    });

    updateSelectedCount();

    document.querySelectorAll(".word-checkbox").forEach((chk) => {
      chk.addEventListener("change", updateSelectedCount);
    });
  }

  function getSelectedIndices(): number[] {
    const checkboxes = document.querySelectorAll(".word-checkbox:checked") as NodeListOf<HTMLInputElement>;
    return Array.from(checkboxes).map((c) => parseInt(c.getAttribute("data-index") || "0", 10));
  }

  function updateSelectedCount() {
    const selected = getSelectedIndices();
    if (selectedCountSpan) {
      selectedCountSpan.innerText = `${selected.length} of ${vocabItems.length} selected`;
    }
  }

  if (selectAllBtn) {
    selectAllBtn.addEventListener("click", () => {
      document.querySelectorAll(".word-checkbox").forEach((c) => ((c as HTMLInputElement).checked = true));
      updateSelectedCount();
    });
  }

  if (deselectAllBtn) {
    deselectAllBtn.addEventListener("click", () => {
      document.querySelectorAll(".word-checkbox").forEach((c) => ((c as HTMLInputElement).checked = false));
      updateSelectedCount();
    });
  }

  async function triggerExport(format: "json" | "anki" | "csv") {
    const indices = getSelectedIndices();
    if (indices.length === 0) {
      alert("Please select at least one word to export.");
      return;
    }

    try {
      const response = await fetch(`/api/tasks/${opId}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format, selected_indices: indices }),
      });

      if (!response.ok) throw new Error("Export failed");

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      if (format === "json") {
        a.download = `vocabcatcher_${opId}.json`;
      } else if (format === "csv") {
        a.download = `vocabcatcher_brainscape_${opId}.csv`;
      } else {
        a.download = `vocabcatcher_${opId}.apkg`;
      }
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(`Export error: ${err.message}`);
    }
  }

  if (exportJsonBtn) exportJsonBtn.addEventListener("click", () => triggerExport("json"));
  if (exportCsvBtn) exportCsvBtn.addEventListener("click", () => triggerExport("csv"));
  if (exportAnkiBtn) exportAnkiBtn.addEventListener("click", () => triggerExport("anki"));

  // Fetch initial metadata and status
  async function loadInitialData() {
    try {
      const resp = await fetch(`/api/tasks/${opId}`);
      if (!resp.ok) return;
      const data: TaskDetails = await resp.json();
      setMetadata(data);
      updateStatus(data.status, data.detail);
      if (data.status === "Ready" && data.items) {
        renderWordList(data.items);
      }
    } catch (e) {
      console.warn("Could not fetch initial task data", e);
    }
  }

  // Real-time Connection: WebSocket with fallback to polling
  function initRealtime() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/operation/${opId}`;

    let socket: WebSocket | null = null;
    let fallbackStarted = false;

    try {
      socket = new WebSocket(wsUrl);

      socket.onopen = () => {
        console.log("WebSocket connected for operation:", opId);
        setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send("ping");
          }
        }, 20000);
      };

      socket.onmessage = (event) => {
        if (event.data === "pong") return;
        try {
          const data: SocketMessage = JSON.parse(event.data);
          updateStatus(data.status, data.detail);

          if (data.status === "Ready" && data.items) {
            renderWordList(data.items);
          } else if (data.status === "Failed") {
            updateStatus("Failed", data.error || "An unexpected error occurred during processing.");
          }
        } catch (e) {
          console.error("Malformed message from server:", e);
        }
      };

      socket.onerror = (err) => {
        console.warn("WebSocket error, falling back to polling", err);
        if (!fallbackStarted) {
          fallbackStarted = true;
          startPolling();
        }
      };

      socket.onclose = () => {
        console.log("Socket connection closed.");
      };
    } catch (e) {
      if (!fallbackStarted) {
        fallbackStarted = true;
        startPolling();
      }
    }
  }

  // Polling fallback
  function startPolling() {
    const timer = setInterval(async () => {
      try {
        const resp = await fetch(`/api/tasks/${opId}`);
        if (!resp.ok) return;
        const task: TaskDetails = await resp.json();
        setMetadata(task);
        updateStatus(task.status, task.detail);
        if (task.status === "Ready" && task.items) {
          clearInterval(timer);
          renderWordList(task.items);
        } else if (task.status === "Failed" || task.status === "Stopped") {
          clearInterval(timer);
        }
      } catch (e) {
        console.error("Polling error", e);
      }
    }, 2500);
  }

  loadInitialData();
  initRealtime();
});
