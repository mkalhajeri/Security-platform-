// Vanilla JS SPA for the incident reporting UI. No build step, no
// dependencies — talks to the FastAPI backend at same-origin /incidents.

const SEVERITIES = ["low", "medium", "high", "critical"];
const STATUSES = ["open", "investigating", "contained", "resolved", "closed"];
const CATEGORIES = [
  "malware",
  "phishing",
  "data_breach",
  "unauthorized_access",
  "denial_of_service",
  "insider_threat",
  "vulnerability",
  "policy_violation",
  "other",
];

const PAGE_SIZE = 20;

const state = {
  offset: 0,
  total: 0,
  items: [],
  selectedId: null,
  filters: { status: "", severity: "", category: "", search: "" },
};

const el = (id) => document.getElementById(id);

function humanize(value) {
  return value.replace(/_/g, " ");
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

function showToast(message, isError = false) {
  const toast = el("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    toast.hidden = true;
  }, 4000);
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`;
    try {
      const body = await resp.json();
      if (body.detail) {
        detail = Array.isArray(body.detail)
          ? body.detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : body.detail;
      }
    } catch (_) {
      // ignore parse errors, fall back to generic message
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

function populateSelect(select, options, { includeEmpty, emptyLabel } = {}) {
  select.innerHTML = "";
  if (includeEmpty) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = emptyLabel || "All";
    select.appendChild(opt);
  }
  for (const value of options) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = humanize(value);
    select.appendChild(opt);
  }
}

function initSelects() {
  populateSelect(el("filter-status"), STATUSES, { includeEmpty: true, emptyLabel: "All statuses" });
  populateSelect(el("filter-severity"), SEVERITIES, { includeEmpty: true, emptyLabel: "All severities" });
  populateSelect(el("filter-category"), CATEGORIES, { includeEmpty: true, emptyLabel: "All categories" });

  populateSelect(el("update-status"), STATUSES);
  populateSelect(el("update-severity"), SEVERITIES);

  populateSelect(el("ni-severity"), SEVERITIES);
  el("ni-severity").value = "medium";
  populateSelect(el("ni-category"), CATEGORIES);
  el("ni-category").value = "other";
}

function buildQuery() {
  const params = new URLSearchParams();
  if (state.filters.status) params.set("status", state.filters.status);
  if (state.filters.severity) params.set("severity", state.filters.severity);
  if (state.filters.category) params.set("category", state.filters.category);
  if (state.filters.search) params.set("search", state.filters.search);
  params.set("limit", PAGE_SIZE);
  params.set("offset", state.offset);
  return params.toString();
}

async function loadIncidents() {
  el("list-loading").hidden = false;
  el("list-empty").hidden = true;
  el("incident-table").hidden = true;
  try {
    const data = await api(`/incidents?${buildQuery()}`);
    state.items = data.items;
    state.total = data.total;
    renderList();
  } catch (err) {
    showToast(`Failed to load incidents: ${err.message}`, true);
  } finally {
    el("list-loading").hidden = true;
  }
}

function renderList() {
  el("total-count").textContent = state.total;

  const tbody = el("incident-rows");
  tbody.innerHTML = "";

  if (state.items.length === 0) {
    el("list-empty").hidden = false;
    el("incident-table").hidden = true;
  } else {
    el("list-empty").hidden = true;
    el("incident-table").hidden = false;

    for (const incident of state.items) {
      const tr = document.createElement("tr");
      tr.dataset.id = incident.id;
      if (incident.id === state.selectedId) tr.classList.add("selected");
      tr.innerHTML = `
        <td>${escapeHtml(incident.title)}</td>
        <td><span class="badge badge-sev-${incident.severity}">${incident.severity}</span></td>
        <td><span class="badge badge-status-${incident.status}">${humanize(incident.status)}</span></td>
        <td><span class="badge badge-neutral">${humanize(incident.category)}</span></td>
        <td>${formatDate(incident.created_at)}</td>
      `;
      tr.addEventListener("click", () => selectIncident(incident.id));
      tbody.appendChild(tr);
    }
  }

  const page = Math.floor(state.offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  el("page-info").textContent = `Page ${page} of ${pageCount}`;
  el("prev-page").disabled = state.offset === 0;
  el("next-page").disabled = state.offset + PAGE_SIZE >= state.total;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function selectIncident(id) {
  state.selectedId = id;
  renderList(); // update row highlighting
  try {
    const incident = await api(`/incidents/${id}`);
    renderDetail(incident);
  } catch (err) {
    showToast(`Failed to load incident: ${err.message}`, true);
  }
}

function renderDetail(incident) {
  el("detail-empty").hidden = true;
  el("detail-content").hidden = false;
  el("detail-content").dataset.id = incident.id;

  el("detail-title").textContent = incident.title;
  el("detail-severity").textContent = incident.severity;
  el("detail-severity").className = `badge badge-sev-${incident.severity}`;
  el("detail-status").textContent = humanize(incident.status);
  el("detail-status").className = `badge badge-status-${incident.status}`;
  el("detail-category").textContent = humanize(incident.category);

  el("detail-description").textContent = incident.description;

  el("detail-reporter").textContent = incident.reporter_email
    ? `${incident.reporter_name} (${incident.reporter_email})`
    : incident.reporter_name;
  el("detail-systems").textContent = incident.affected_systems.length
    ? incident.affected_systems.join(", ")
    : "—";
  el("detail-tags").textContent = incident.tags.length ? incident.tags.join(", ") : "—";
  el("detail-created").textContent = formatDate(incident.created_at);
  el("detail-updated").textContent = formatDate(incident.updated_at);
  el("detail-resolved").textContent = formatDate(incident.resolved_at);

  el("update-status").value = incident.status;
  el("update-severity").value = incident.severity;

  const timeline = el("detail-timeline");
  timeline.innerHTML = "";
  const entries = [...incident.timeline].reverse();
  for (const entry of entries) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="tl-meta">${formatDate(entry.timestamp)} · ${escapeHtml(entry.actor)} · ${escapeHtml(entry.action)}</span>
      ${entry.note ? escapeHtml(entry.note) : ""}
    `;
    timeline.appendChild(li);
  }
}

async function saveUpdates() {
  const id = el("detail-content").dataset.id;
  if (!id) return;
  const updates = {
    status: el("update-status").value,
    severity: el("update-severity").value,
  };
  try {
    const updated = await api(`/incidents/${id}`, {
      method: "PATCH",
      body: JSON.stringify(updates),
    });
    renderDetail(updated);
    showToast("Incident updated.");
    loadIncidents();
  } catch (err) {
    showToast(`Update failed: ${err.message}`, true);
  }
}

async function addComment(evt) {
  evt.preventDefault();
  const id = el("detail-content").dataset.id;
  if (!id) return;
  const payload = {
    actor: el("comment-actor").value.trim(),
    action: el("comment-action").value.trim() || "comment",
    note: el("comment-note").value.trim() || null,
  };
  try {
    const updated = await api(`/incidents/${id}/timeline`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderDetail(updated);
    el("comment-note").value = "";
    showToast("Added to timeline.");
  } catch (err) {
    showToast(`Failed to add entry: ${err.message}`, true);
  }
}

async function deleteIncident() {
  const id = el("detail-content").dataset.id;
  if (!id) return;
  if (!confirm("Delete this incident? This cannot be undone.")) return;
  try {
    await api(`/incidents/${id}`, { method: "DELETE" });
    el("detail-content").hidden = true;
    el("detail-empty").hidden = false;
    state.selectedId = null;
    showToast("Incident deleted.");
    loadIncidents();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, true);
  }
}

function openModal() {
  el("new-incident-modal").hidden = false;
}

function closeModal() {
  el("new-incident-modal").hidden = true;
  el("new-incident-form").reset();
  el("ni-severity").value = "medium";
  el("ni-category").value = "other";
}

async function submitNewIncident(evt) {
  evt.preventDefault();
  const payload = {
    title: el("ni-title").value.trim(),
    description: el("ni-description").value.trim(),
    severity: el("ni-severity").value,
    category: el("ni-category").value,
    reporter_name: el("ni-reporter-name").value.trim(),
    reporter_email: el("ni-reporter-email").value.trim() || null,
    affected_systems: splitCsv(el("ni-systems").value),
    tags: splitCsv(el("ni-tags").value),
  };
  try {
    await api("/incidents", { method: "POST", body: JSON.stringify(payload) });
    closeModal();
    showToast("Incident reported.");
    state.offset = 0;
    loadIncidents();
  } catch (err) {
    showToast(`Failed to report incident: ${err.message}`, true);
  }
}

function splitCsv(value) {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function wireEvents() {
  let searchDebounce;
  el("filter-search").addEventListener("input", (e) => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      state.filters.search = e.target.value.trim();
      state.offset = 0;
      loadIncidents();
    }, 300);
  });

  el("filter-status").addEventListener("change", (e) => {
    state.filters.status = e.target.value;
    state.offset = 0;
    loadIncidents();
  });
  el("filter-severity").addEventListener("change", (e) => {
    state.filters.severity = e.target.value;
    state.offset = 0;
    loadIncidents();
  });
  el("filter-category").addEventListener("change", (e) => {
    state.filters.category = e.target.value;
    state.offset = 0;
    loadIncidents();
  });

  el("prev-page").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - PAGE_SIZE);
    loadIncidents();
  });
  el("next-page").addEventListener("click", () => {
    state.offset += PAGE_SIZE;
    loadIncidents();
  });

  el("new-incident-btn").addEventListener("click", openModal);
  el("close-modal-btn").addEventListener("click", closeModal);
  el("cancel-new-incident").addEventListener("click", closeModal);
  el("new-incident-form").addEventListener("submit", submitNewIncident);

  el("save-updates-btn").addEventListener("click", saveUpdates);
  el("comment-form").addEventListener("submit", addComment);
  el("delete-incident-btn").addEventListener("click", deleteIncident);
}

initSelects();
wireEvents();
loadIncidents();
