(() => {
  const state = { adminToken: null, polling: null };
  const $ = (id) => document.getElementById(id);

  const userModeButton = $("userModeButton");
  const adminModeButton = $("adminModeButton");
  const userPanel = $("userPanel");
  const adminPanel = $("adminPanel");
  const sessionLabel = $("sessionLabel");
  const logoutButton = $("logoutButton");
  const loginCard = $("loginCard");
  const adminWorkspace = $("adminWorkspace");

  function setMode(mode) {
    const admin = mode === "admin";
    userModeButton?.classList.toggle("active", !admin);
    adminModeButton?.classList.toggle("active", admin);
    userPanel.classList.toggle("hidden", admin);
    adminPanel.classList.toggle("hidden", !admin);
    sessionLabel.textContent = admin && state.adminToken ? "Admin session" : "User search";
  }

  userModeButton?.addEventListener("click", () => setMode("user"));
  adminModeButton?.addEventListener("click", () => setMode("admin"));
  logoutButton.addEventListener("click", () => {
    state.adminToken = null;
    loginCard.classList.remove("hidden");
    adminWorkspace.classList.add("hidden");
    logoutButton.classList.add("hidden");
    setMode("user");
  });

  $("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = $("loginError");
    error.classList.add("hidden");
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: $("usernameInput").value,
          password: $("passwordInput").value,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Unable to sign in");
      state.adminToken = payload.access_token;
      loginCard.classList.add("hidden");
      adminWorkspace.classList.remove("hidden");
      logoutButton.classList.remove("hidden");
      sessionLabel.textContent = "Admin session";
      loadDocuments();
    } catch (err) {
      error.textContent = err.message;
      error.classList.remove("hidden");
    }
  });

  $("fileInput").addEventListener("change", () => {
    const file = $("fileInput").files[0];
    const selected = $("selectedFile");
    selected.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "";
    selected.classList.toggle("hidden", !file);
  });

  $("uploadForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("fileInput").files[0];
    if (!file || !state.adminToken) return;
    const message = $("uploadMessage");
    message.textContent = "Uploading document…";
    message.className = "message";
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/v1/documents", {
        method: "POST",
        headers: { Authorization: `Bearer ${state.adminToken}` },
        body,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Upload failed");
      message.textContent = `Accepted: ${payload.name}. Ingestion has started.`;
      monitorDocument(payload.document_id);
      loadDocuments();
      $("fileInput").value = "";
      $("selectedFile").classList.add("hidden");
    } catch (err) {
      message.textContent = err.message;
      message.className = "error-message";
    }
  });

  async function monitorDocument(documentId) {
    const status = $("documentStatus");
    status.classList.remove("hidden");
    if (state.polling) clearInterval(state.polling);
    const check = async () => {
      try {
        const response = await fetch(`/api/v1/documents/${documentId}`, {
          headers: { Authorization: `Bearer ${state.adminToken}` },
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Status unavailable");
        if (payload.progress !== undefined) {
          renderIngestionProgress(payload);
        }
        const statusClass = `status-${payload.status}`;
        status.className = `document-status ${statusClass}`;
        status.innerHTML = `<strong>${escapeHtml(payload.name)}</strong> · ${escapeHtml(payload.status)}${payload.page_count ? ` · ${payload.page_count} pages` : ""}${payload.error_message ? `<br><small>${escapeHtml(payload.error_message)}</small>` : ""}`;
        if (["completed", "failed"].includes(payload.status)) {
          clearInterval(state.polling);
          state.polling = null;
        }
      } catch (err) {
        status.className = "document-status status-failed";
        status.textContent = err.message;
        clearInterval(state.polling);
        state.polling = null;
      }
    };
    await check();
    state.polling = setInterval(check, 3000);
  }

  async function loadDocuments() {
    if (!state.adminToken) return;
    const container = $("documentList");
    try {
      const response = await fetch("/api/v1/documents", { headers: { Authorization: `Bearer ${state.adminToken}` } });
      const documents = await response.json();
      if (!response.ok) throw new Error(documents.detail || "Unable to load documents");
      container.classList.remove("hidden");
      if (!documents.length) {
        container.innerHTML = `<div class="document-list-empty">No uploaded documents.</div>`;
        return;
      }
      container.innerHTML = `<div class="document-list-title">Uploaded documents</div>` + documents.map((document) => `<div class="document-row"><div><strong>${escapeHtml(document.name)}</strong><small class="document-row-status status-${escapeHtml(document.status)}">${escapeHtml(document.status)}</small>${document.error_message ? `<small class="document-row-error">${escapeHtml(document.error_message)}</small>` : ""}</div><button class="button button-danger delete-document" type="button" data-document-id="${escapeHtml(document.document_id)}" data-document-name="${escapeHtml(document.name)}">Delete</button></div>`).join("");
      container.querySelectorAll(".delete-document").forEach((button) => button.addEventListener("click", () => deleteDocument(button.dataset.documentId, button.dataset.documentName)));
    } catch (err) {
      container.classList.remove("hidden");
      container.innerHTML = `<div class="error-message">${escapeHtml(err.message)}</div>`;
    }
  }

  async function deleteDocument(documentId, documentName) {
    if (!state.adminToken || !window.confirm(`Delete '${documentName}' and all related data? This cannot be undone.`)) return;
    try {
      const response = await fetch(`/api/v1/documents/${encodeURIComponent(documentId)}`, { method: "DELETE", headers: { Authorization: `Bearer ${state.adminToken}` } });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Delete failed");
      if ($("documentStatus")) $("documentStatus").classList.add("hidden");
      if ($("validationReport")) $("validationReport").classList.add("hidden");
      await loadDocuments();
    } catch (err) {
      window.alert(err.message);
    }
  }

  $("queryForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = $("queryInput").value.trim();
    if (!query) return;
    $("queryLoading").classList.remove("hidden");
    $("answerCard").classList.add("hidden");
    try {
      const response = await fetch("/api/v1/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, top_k: 5 }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Search failed");
      renderAnswer(payload);
    } catch (err) {
      renderAnswer({ answer: err.message, confidence: "Low", sources: [], reasoning_summary: "" });
    } finally {
      $("queryLoading").classList.add("hidden");
    }
  });

  function renderAnswer(payload) {
    const card = $("answerCard");
    card.innerHTML = `<div class="answer-text">${escapeHtml(payload.answer || "No answer returned.")}</div>`;
    card.classList.remove("hidden");
  }

  $("validateButton").addEventListener("click", async () => {
    if (!state.adminToken) return;
    const button = $("validateButton");
    const message = $("validationMessage");
    const report = $("validationReport");
    button.disabled = true;
    button.textContent = "Validating…";
    message.textContent = "Auditing documents, chunks, embeddings, retrieval, answers, and citations…";
    report.classList.add("hidden");
    try {
      const response = await fetch("/api/v1/admin/validate", {
        method: "POST",
        headers: { Authorization: `Bearer ${state.adminToken}` },
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Validation failed");
      renderValidationReport(payload);
      message.textContent = "Validation complete";
    } catch (err) {
      message.textContent = err.message;
      report.classList.remove("hidden");
      report.innerHTML = `<div class="error-message">${escapeHtml(err.message)}</div>`;
    } finally {
      button.disabled = false;
      button.textContent = "Validate knowledge base";
    }
  });

  function renderValidationReport(payload) {
    const report = $("validationReport");
    const scores = Object.entries(payload.scores || {}).map(([label, value]) => `<div class="validation-score"><span>${escapeHtml(label)}</span><strong>${Number(value).toFixed(1)}%</strong></div>`).join("");
    const blockers = (payload.production_blockers || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>None reported</li>";
    const failures = (payload.top_retrieval_failures || []).slice(0, 3).map((item) => `<li><strong>${escapeHtml(item.question)}</strong><br><small>${escapeHtml((item.failure_evidence || []).join("; "))}</small></li>`).join("") || "<li>None</li>";
    const missing = (payload.missing_knowledge_areas || []).slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>None</li>";
    report.innerHTML = `<div class="validation-header"><div><span class="eyebrow">Validation report</span><h3>${escapeHtml(payload.production_readiness || "Knowledge Base Requires Attention")}</h3><p>${escapeHtml(payload.final_status || "Requires Review")} · Overall health ${Number(payload.overall_health_score || 0).toFixed(1)}%</p></div><strong>${payload.documents_processed || 0}/${payload.documents_total || 0} documents</strong></div><div class="validation-counts"><span>${payload.chunks_created || 0} chunks</span><span>${payload.embeddings_generated || 0} embeddings</span><span>${payload.generated_question_count || 0} test questions</span></div><div class="validation-scores">${scores}</div><div class="validation-columns"><div><h4>Production blockers</h4><ul>${blockers}</ul></div><div><h4>Top retrieval failures</h4><ol>${failures}</ol></div><div><h4>Missing knowledge areas</h4><ul>${missing}</ul></div></div><details><summary>Full validation JSON</summary><pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre></details>`;
    report.classList.remove("hidden");
  }

  function renderIngestionProgress(payload) {
    const container = $("ingestionProgress");
    const stages = payload.stages || [];
    container.classList.remove("hidden");
    container.innerHTML = `<div class="progress-heading"><strong>Ingestion progress</strong><span>${payload.progress || 0}%</span></div><div class="progress-track"><span style="width:${payload.progress || 0}%"></span></div><div class="stage-list">${stages.map((stage) => {
      const icon = stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : stage.status === "running" ? "◌" : "–";
      return `<div class="ingestion-stage stage-${stage.status}"><span class="stage-icon">${icon}</span><div><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.message || stage.status)}</small></div>${stage.status === "running" ? `<span class="stage-spinner"></span>` : ""}</div>`;
    }).join("")}</div><div class="ready-state ${payload.status === "completed" ? "ready" : ""}">${payload.status === "completed" ? "✓ Ready for questions" : ""}</div>`;
  }

  document.querySelector(".mode-switch")?.classList.add("hidden");

  if (window.location.pathname === "/admin") {
    setMode("admin");
  }


  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }
})();
