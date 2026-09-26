interface OperationSummary {
  id: string;
  status: string;
  status_detail: string;
  target_language: string;
  native_language: string;
  cefr_level: string;
  source: string;
  items_count: number;
  error?: string | null;
  created_at: number;
}

document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("operations-container") as HTMLElement;
  const refreshBtn = document.getElementById("refresh-btn") as HTMLButtonElement;
  const statsSummary = document.getElementById("stats-summary") as HTMLElement;

  let pollInterval: any = null;

  function getStatusBadge(status: string): string {
    switch (status) {
      case "Ready":
        return '<span class="badge badge-success text-white">Ready</span>';
      case "Failed":
        return '<span class="badge badge-error text-white">Failed</span>';
      case "Stopped":
        return '<span class="badge badge-warning text-white">Stopped</span>';
      case "Queued":
        return '<span class="badge badge-neutral">Queued</span>';
      default:
        return `<span class="badge badge-primary gap-1"><span class="loading loading-spinner loading-xs"></span>${status}</span>`;
    }
  }

  function formatTime(timestamp: number): string {
    if (!timestamp) return "Just now";
    const date = new Date(timestamp * 1000);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  async function fetchOperations() {
    try {
      const res = await fetch("/api/operations");
      if (!res.ok) throw new Error(`HTTP error: ${res.status}`);
      const data: OperationSummary[] = await res.json();
      renderOperations(data);
    } catch (err: any) {
      if (container) {
        container.innerHTML = `
          <div class="alert alert-error">
            <span>Failed to load operations: ${err.message}</span>
          </div>
        `;
      }
    }
  }

  async function stopTask(opId: string, btn: HTMLButtonElement) {
    btn.disabled = true;
    btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span> Stopping...';
    try {
      const res = await fetch(`/api/tasks/${opId}/stop`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to stop operation");
      await fetchOperations();
    } catch (e: any) {
      alert(`Could not stop task: ${e.message}`);
      btn.disabled = false;
      btn.innerText = "Stop";
    }
  }

  function renderOperations(operations: OperationSummary[]) {
    if (!container) return;

    if (statsSummary) {
      const activeCount = operations.filter(
        (o) => o.status !== "Ready" && o.status !== "Failed" && o.status !== "Stopped"
      ).length;
      statsSummary.innerText = `Total: ${operations.length} | Running / Active: ${activeCount}`;
    }

    if (operations.length === 0) {
      container.innerHTML = `
        <div class="card bg-base-100 shadow border border-base-300 p-8 text-center text-base-content/70">
          <p class="font-medium text-lg">No operations currently in memory.</p>
          <p class="text-sm mt-1">Submit content on the home page to start a new vocabulary analysis.</p>
          <div class="mt-4">
            <a href="/" class="btn btn-primary btn-sm">Start New Task</a>
          </div>
        </div>
      `;
      return;
    }

    container.innerHTML = "";

    operations.forEach((op) => {
      const card = document.createElement("div");
      card.className = "card bg-base-100 shadow border border-base-300 p-5 transition-all hover:border-primary/50";

      const isRunning =
        op.status !== "Ready" && op.status !== "Failed" && op.status !== "Stopped";

      card.innerHTML = `
        <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div class="space-y-1 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-mono text-sm font-bold">${op.id.substring(0, 8)}...</span>
              ${getStatusBadge(op.status)}
              <span class="badge badge-ghost badge-sm">Source: ${op.target_language}</span>
              <span class="badge badge-ghost badge-sm">Definitions: ${op.native_language}</span>
              <span class="badge badge-outline badge-sm">Level: ${op.cefr_level}</span>
              <span class="text-xs text-base-content/50 ml-auto md:ml-0">${formatTime(op.created_at)}</span>
            </div>

            <div class="text-sm font-medium mt-1">
              <span class="text-base-content/60">Input:</span> <span class="break-all">${op.source}</span>
            </div>

            <div class="text-xs text-base-content/70">
              ${op.status_detail}
              ${op.items_count > 0 ? ` &bull; <strong>${op.items_count} words ready</strong>` : ""}
            </div>

            ${op.error ? `<div class="text-xs text-error mt-1">${op.error}</div>` : ""}
          </div>

          <div class="flex items-center gap-2 self-end md:self-center">
            ${
              isRunning
                ? `<button class="btn btn-sm btn-error btn-outline stop-btn" data-id="${op.id}">Stop</button>`
                : ""
            }
            <a href="/operation/${op.id}" class="btn btn-sm btn-primary">
              View &rarr;
            </a>
          </div>
        </div>
      `;

      container.appendChild(card);
    });

    document.querySelectorAll(".stop-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const target = e.currentTarget as HTMLButtonElement;
        const opId = target.getAttribute("data-id");
        if (opId) stopTask(opId, target);
      });
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => fetchOperations());
  }

  // Initial load
  fetchOperations();

  // Polling every 2.5 seconds
  pollInterval = setInterval(fetchOperations, 2500);

  window.addEventListener("beforeunload", () => {
    if (pollInterval) clearInterval(pollInterval);
  });
});
