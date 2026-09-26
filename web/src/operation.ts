interface VocabItem {
  infinitive?: string;
  phrasal_verb?: string;
  native_language_definition: string;
  from_source: string[];
  example_sentence: string;
}

interface SocketMessage {
  status: "Queued" | "Parsing" | "Phase 1: Extracting Words" | "Phase 2: Translating in Context" | "Ready" | "Failed";
  detail: string;
  items?: VocabItem[];
  error?: string;
}

document.addEventListener("DOMContentLoaded", () => {
  const pathParts = window.location.pathname.split("/").filter(Boolean);
  const opId = pathParts[pathParts.length - 1];

  const statusBadge = document.getElementById("status-badge") as HTMLElement;
  const statusDetail = document.getElementById("status-detail") as HTMLElement;
  const progressBar = document.getElementById("progress-bar") as HTMLProgressElement;
  const loadingContainer = document.getElementById("loading-container") as HTMLElement;
  const resultsContainer = document.getElementById("results-container") as HTMLElement;
  const wordListContainer = document.getElementById("word-list") as HTMLElement;
  const selectAllBtn = document.getElementById("select-all-btn") as HTMLButtonElement;
  const deselectAllBtn = document.getElementById("deselect-all-btn") as HTMLButtonElement;
  const exportJsonBtn = document.getElementById("export-json-btn") as HTMLButtonElement;
  const exportAnkiBtn = document.getElementById("export-anki-btn") as HTMLButtonElement;
  const selectedCountSpan = document.getElementById("selected-count") as HTMLElement;

  let vocabItems: VocabItem[] = [];

  function updateStatus(status: string, detail: string) {
    if (statusBadge) statusBadge.innerText = status;
    if (statusDetail) statusDetail.innerText = detail;

    if (progressBar) {
      if (status.includes("Phase 1")) {
        progressBar.value = 40;
      } else if (status.includes("Phase 2")) {
        progressBar.value = 75;
      } else if (status === "Ready") {
        progressBar.value = 100;
      } else if (status === "Failed") {
        progressBar.classList.add("progress-error");
      }
    }
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

  async function triggerExport(format: "json" | "anki") {
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
      a.download = format === "json" ? `vocabcatcher_${opId}.json` : `vocabcatcher_${opId}.apkg`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(`Export error: ${err.message}`);
    }
  }

  if (exportJsonBtn) exportJsonBtn.addEventListener("click", () => triggerExport("json"));
  if (exportAnkiBtn) exportAnkiBtn.addEventListener("click", () => triggerExport("anki"));

  // Real-time Connection: WebTransport with transparent fallback to WebSocket
  async function initRealtime() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/operation/${opId}`;

    let socket: WebSocket | null = null;

    // Check if WebTransport is available in the browser
    if ("WebTransport" in window) {
      console.info("WebTransport supported by client. Checking endpoint capability...");
    }

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
      startPolling();
    };

    socket.onclose = () => {
      console.log("Socket connection closed.");
    };
  }

  // Polling fallback
  function startPolling() {
    const timer = setInterval(async () => {
      try {
        const resp = await fetch(`/api/tasks/${opId}`);
        if (!resp.ok) return;
        const task = await resp.json();
        updateStatus(task.status, task.detail);
        if (task.status === "Ready") {
          clearInterval(timer);
          renderWordList(task.items);
        } else if (task.status === "Failed") {
          clearInterval(timer);
        }
      } catch (e) {
        console.error("Polling error", e);
      }
    }, 2500);
  }

  initRealtime();
});
