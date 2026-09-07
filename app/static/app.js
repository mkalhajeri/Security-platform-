// Vanilla JS SPA for physical security incident reporting. No build step,
// no dependencies — talks to the FastAPI backend at same-origin /incidents.

const STATUSES = ["reported", "under_review", "closed"];
const NATURE_OF_REPORT = ["incident", "accident", "near_miss", "security", "other"];
const REPORTED_VIA = ["email", "phone_call", "sms", "whatsapp", "other"];

// [value, label] — labels/numbering match Section 4 of the paper form.
const INCIDENT_CATEGORIES = [
  ["vehicle_accident", "01 Vehicle Accident"],
  ["injury", "02 Injury / Personal Injury"],
  ["illness_medical", "03 Illness / Medical Case"],
  ["fire_explosion", "04 Fire / Explosion"],
  ["theft", "05 Theft"],
  ["attempted_theft", "06 Attempted Theft"],
  ["vandalism_damage", "07 Vandalism / Damage"],
  ["property_damage", "08 Property Damage"],
  ["security_breach", "09 Security Breach"],
  ["harassment", "10 Harassment"],
  ["verbal_abuse", "11 Verbal Abuse"],
  ["physical_assault", "12 Physical Assault"],
  ["drug_alcohol", "13 Drug / Alcohol Related"],
  ["traffic_violation", "14 Traffic Violation"],
  ["environmental", "15 Environmental"],
  ["fall_from_height", "16 Fall from Height"],
  ["electrical", "17 Electrical"],
  ["chemical_spill", "18 Chemical Spill"],
  ["equipment_failure", "19 Equipment Failure"],
  ["other", "20 Others"],
];

const SUPPORTING_DOCUMENTS = [
  ["photos", "Photos"],
  ["cctv_footage", "CCTV Footage"],
  ["medical_report", "Medical Report"],
  ["witness_statement", "Witness Statement"],
  ["police_report", "Police Report"],
  ["vehicle_report", "Vehicle Report"],
  ["maintenance_report", "Maintenance Report"],
  ["other", "Other"],
];

const CATEGORY_LABELS = Object.fromEntries(INCIDENT_CATEGORIES);
const DOCUMENT_LABELS = Object.fromEntries(SUPPORTING_DOCUMENTS);

const APPROVAL_FIELDS = [
  ["prepared_by", "Prepared By", "Security Officer"],
  ["reviewed_by", "Reviewed By", "Security Supervisor"],
  ["approved_by", "Approved By", "Management"],
];

const PAGE_SIZE = 20;

const state = {
  offset: 0,
  total: 0,
  items: [],
  selectedId: null,
  filters: { status: "", category: "", site_location: "", search: "" },
  personRowCount: 0,
};

const el = (id) => document.getElementById(id);

function humanize(value) {
  return (value || "").replace(/_/g, " ");
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString();
}

function formatDateOnly(iso) {
  if (!iso) return "—";
  return iso;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
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
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`;
    try {
      const body = await resp.json();
      if (body.detail) {
        detail = Array.isArray(body.detail)
          ? body.detail.map((d) => (d.loc ? d.loc.join(".") + ": " : "") + (d.msg || JSON.stringify(d))).join("; ")
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

function apiJson(path, options = {}) {
  return api(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
}

function populateSelect(select, values, { includeEmpty, emptyLabel, labels } = {}) {
  select.innerHTML = "";
  if (includeEmpty) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = emptyLabel || "All";
    select.appendChild(opt);
  }
  for (const value of values) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = labels ? labels[value] || humanize(value) : humanize(value);
    select.appendChild(opt);
  }
}

function buildCheckboxGrid(container, options, namePrefix) {
  container.innerHTML = "";
  for (const [value, label] of options) {
    const wrap = document.createElement("label");
    wrap.className = "checkbox-item";
    wrap.innerHTML = `<input type="checkbox" value="${value}" data-group="${namePrefix}"> ${escapeHtml(label)}`;
    container.appendChild(wrap);
  }
}

function getCheckedValues(container) {
  return Array.from(container.querySelectorAll("input[type=checkbox]:checked")).map((i) => i.value);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------

function showTab(tab) {
  el("view-queue").hidden = tab !== "queue";
  el("view-new").hidden = tab !== "new";
  el("view-analytics").hidden = tab !== "analytics";
  el("tab-queue").classList.toggle("active", tab === "queue");
  el("tab-new").classList.toggle("active", tab === "new");
  el("tab-analytics").classList.toggle("active", tab === "analytics");
  if (tab === "analytics") loadAnalytics();
}

// ---------------------------------------------------------------------
// Queue list
// ---------------------------------------------------------------------

function buildQuery() {
  const params = new URLSearchParams();
  if (state.filters.status) params.set("status", state.filters.status);
  if (state.filters.category) params.set("category", state.filters.category);
  if (state.filters.site_location) params.set("site_location", state.filters.site_location);
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
    const data = await apiJson(`/incidents?${buildQuery()}`);
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
      const categoryLabels = (incident.incident_categories || []).map((c) => CATEGORY_LABELS[c] || c).join(", ");
      tr.innerHTML = `
        <td data-label="Incident">
          <span class="row-title">${escapeHtml(incident.site_location)}</span>
          <span class="row-sub">${escapeHtml(categoryLabels || incident.incident_type_summary || "—")}</span>
        </td>
        <td data-label="Nature"><span class="badge badge-nature-${incident.nature_of_report}">${humanize(incident.nature_of_report)}</span></td>
        <td data-label="Status"><span class="badge badge-status-${incident.status}">${humanize(incident.status)}</span></td>
        <td data-label="Reported">${formatDate(incident.created_at)}</td>
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

// ---------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------

async function selectIncident(id) {
  state.selectedId = id;
  renderList();
  try {
    const incident = await apiJson(`/incidents/${id}`);
    renderDetail(incident);
  } catch (err) {
    showToast(`Failed to load incident: ${err.message}`, true);
  }
}

function renderAttachmentList(container, attachments, incidentId) {
  container.innerHTML = "";
  if (!attachments || attachments.length === 0) {
    container.innerHTML = '<span class="hint">None yet.</span>';
    return;
  }
  for (const a of attachments) {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";
    chip.innerHTML = `
      <a href="/incidents/${incidentId}/attachments/${a.id}" target="_blank" rel="noopener" title="${escapeHtml(a.filename)}">${escapeHtml(a.filename)}</a>
      <button type="button" title="Remove" data-attachment-id="${a.id}">✕</button>
    `;
    chip.querySelector("button").addEventListener("click", () => deleteAttachment(incidentId, a.id));
    container.appendChild(chip);
  }
}

function renderApprovals(incident) {
  const grid = el("approvals-grid");
  grid.innerHTML = "";

  for (const [field, label, roleLabel] of APPROVAL_FIELDS) {
    const value = incident[field];
    const card = document.createElement("div");
    card.className = "approval-card";
    card.innerHTML = `
      <h4>${label} (${roleLabel})</h4>
      <dl ${value ? "" : "hidden"}>
        <div><dt>Name</dt><dd>${escapeHtml(value && value.name)}</dd></div>
        <div><dt>Position</dt><dd>${escapeHtml(value && value.position)}</dd></div>
        <div><dt>Date</dt><dd>${escapeHtml(value && value.signed_date)}</dd></div>
        ${value && !value.signature_image ? `<div><dt>Signature</dt><dd>${escapeHtml(value.signature)}</dd></div>` : ""}
      </dl>
      ${value && value.signature_image ? `<img class="signature-preview" src="${value.signature_image}" alt="${label} signature">` : ""}
      <p class="hint" ${value ? "hidden" : ""}>Not yet signed.</p>
      <div style="margin-top:0.5rem;">
        <button type="button" class="btn btn-sm" data-sign="${field}">${value ? "Edit sign-off" : "Sign"}</button>
      </div>
    `;
    grid.appendChild(card);
  }

  grid.querySelectorAll("[data-sign]").forEach((btn) => {
    btn.addEventListener("click", () => openApprovalSignature(btn.dataset.sign));
  });
}

function openApprovalSignature(field) {
  const incident = currentIncident;
  if (!incident) return;
  const [, label, roleLabel] = APPROVAL_FIELDS.find(([f]) => f === field);
  const existing = incident[field] || {};
  openSignatureModal({
    title: `${label} (${roleLabel})`,
    initial: existing,
    onSave: async (result) => {
      try {
        const updated = await apiJson(`/incidents/${incident.id}`, {
          method: "PATCH",
          body: JSON.stringify({ [field]: result }),
        });
        renderDetail(updated);
        showToast("Sign-off saved.");
      } catch (err) {
        showToast(`Failed to save: ${err.message}`, true);
      }
    },
  });
}

let currentIncident = null;

function renderDetail(incident) {
  currentIncident = incident;
  el("detail-empty").hidden = true;
  el("detail-content").hidden = false;
  el("detail-content").dataset.id = incident.id;

  el("detail-title").textContent = incident.site_location;
  const statusBadge = el("detail-status");
  statusBadge.textContent = humanize(incident.status);
  statusBadge.className = `badge badge-status-${incident.status}`;
  const natureBadge = el("detail-nature");
  natureBadge.textContent = humanize(incident.nature_of_report);
  natureBadge.className = `badge badge-nature-${incident.nature_of_report}`;

  el("d-department").textContent = incident.department_area || "—";
  el("d-reported-by").textContent = [incident.reported_by, incident.reported_by_job_title].filter(Boolean).join(" · ") || "—";
  el("d-reported-via").textContent = humanize(incident.reported_via) + (incident.reported_via_other ? ` (${incident.reported_via_other})` : "");
  el("d-report-datetime").textContent = `${incident.report_date} ${incident.report_time}`;
  el("d-exact-location").textContent = incident.exact_location;
  el("d-incident-datetime").textContent = `${incident.incident_date} ${incident.incident_time}`;

  const catWrap = el("d-categories");
  catWrap.innerHTML = "";
  const cats = incident.incident_categories || [];
  if (cats.length === 0) {
    catWrap.innerHTML = '<span class="hint">None selected.</span>';
  } else {
    cats.forEach((c) => {
      const span = document.createElement("span");
      span.className = "badge badge-neutral";
      span.textContent = CATEGORY_LABELS[c] || humanize(c);
      catWrap.appendChild(span);
    });
    if (incident.incident_category_other) {
      const span = document.createElement("span");
      span.className = "badge badge-neutral";
      span.textContent = `Other: ${incident.incident_category_other}`;
      catWrap.appendChild(span);
    }
  }

  const personsBody = el("d-persons-rows");
  personsBody.innerHTML = "";
  const persons = incident.involved_persons || [];
  if (persons.length === 0) {
    personsBody.innerHTML = '<tr><td colspan="7" class="hint">None recorded.</td></tr>';
  } else {
    persons.forEach((p) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td data-label="Name">${escapeHtml(p.name)}</td>
        <td data-label="Role">${escapeHtml(p.designation)}</td>
        <td data-label="Company">${escapeHtml(p.company)}</td>
        <td data-label="ID / Labour card">${escapeHtml(p.id_number)}</td>
        <td data-label="Nationality">${escapeHtml(p.nationality)}</td>
        <td data-label="Contact">${escapeHtml(p.contact_no)}</td>
        <td data-label="Gender">${escapeHtml(p.gender)}</td>
      `;
      personsBody.appendChild(tr);
    });
  }
  el("d-witnesses").textContent = incident.witnesses ? `Witness(es): ${incident.witnesses}` : "";

  el("d-background").textContent = incident.incident_background || "—";
  el("d-action").textContent = incident.immediate_action_taken || "—";
  el("d-root-cause").textContent = incident.root_cause || "—";
  el("d-recommendations").textContent = incident.recommendations || "—";
  el("d-authorities").textContent = incident.local_authorities_involvement || "—";

  renderAttachmentList(el("d-pictures"), incident.incident_pictures, incident.id);
  renderAttachmentList(el("d-documents"), incident.supporting_document_files, incident.id);

  const suppWrap = el("d-supporting-checklist");
  suppWrap.innerHTML = "";
  const supp = incident.supporting_documents || [];
  if (supp.length === 0) {
    suppWrap.innerHTML = '<span class="hint">None checked.</span>';
  } else {
    supp.forEach((s) => {
      const span = document.createElement("span");
      span.className = "badge badge-neutral";
      span.textContent = DOCUMENT_LABELS[s] || humanize(s);
      suppWrap.appendChild(span);
    });
  }

  el("update-status").value = incident.status;

  renderApprovals(incident);

  const timeline = el("detail-timeline");
  timeline.innerHTML = "";
  const entries = [...(incident.timeline || [])].reverse();
  for (const entry of entries) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="tl-meta">${formatDate(entry.timestamp)} · ${escapeHtml(entry.actor)} · ${escapeHtml(entry.action)}</span>
      ${entry.note ? escapeHtml(entry.note) : ""}
    `;
    timeline.appendChild(li);
  }
}

async function saveStatus() {
  const id = el("detail-content").dataset.id;
  if (!id) return;
  try {
    const updated = await apiJson(`/incidents/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: el("update-status").value }),
    });
    renderDetail(updated);
    showToast("Status updated.");
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
    const updated = await apiJson(`/incidents/${id}/timeline`, { method: "POST", body: JSON.stringify(payload) });
    renderDetail(updated);
    el("comment-note").value = "";
    showToast("Added to timeline.");
  } catch (err) {
    showToast(`Failed to add entry: ${err.message}`, true);
  }
}

async function deleteIncidentHandler() {
  const id = el("detail-content").dataset.id;
  if (!id) return;
  if (!confirm("Delete this incident report? This cannot be undone.")) return;
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

async function uploadAttachment(kind) {
  const id = el("detail-content").dataset.id;
  if (!id) return;
  const inputId = kind === "picture" ? "upload-picture-input" : "upload-document-input";
  const input = el(inputId);
  const file = input.files[0];
  if (!file) {
    showToast("Choose a file first.", true);
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  try {
    const updated = await api(`/incidents/${id}/attachments?kind=${kind}`, {
      method: "POST",
      body: formData,
    });
    renderDetail(updated);
    input.value = "";
    showToast("Attachment uploaded.");
  } catch (err) {
    showToast(`Upload failed: ${err.message}`, true);
  }
}

async function deleteAttachment(incidentId, attachmentId) {
  if (!confirm("Remove this attachment?")) return;
  try {
    const updated = await api(`/incidents/${incidentId}/attachments/${attachmentId}`, { method: "DELETE" });
    renderDetail(updated);
    showToast("Attachment removed.");
  } catch (err) {
    showToast(`Failed to remove: ${err.message}`, true);
  }
}

// ---------------------------------------------------------------------
// Signature pad (canvas drawing, used by both approvals and the new-report
// "prepared by" section). No persistent per-person signature library yet —
// that needs real user accounts — so each sign-off is captured fresh.
// ---------------------------------------------------------------------

const sigPad = {
  canvas: null,
  ctx: null,
  drawing: false,
  hasContent: false,
  onSave: null,
};

function sigCanvasPoint(evt) {
  const rect = sigPad.canvas.getBoundingClientRect();
  const scaleX = sigPad.canvas.width / rect.width;
  const scaleY = sigPad.canvas.height / rect.height;
  return { x: (evt.clientX - rect.left) * scaleX, y: (evt.clientY - rect.top) * scaleY };
}

function sigClear() {
  const { ctx, canvas } = sigPad;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  sigPad.hasContent = false;
}

function initSignaturePad() {
  const canvas = el("signature-canvas");
  canvas.width = 600;
  canvas.height = 180;
  sigPad.canvas = canvas;
  sigPad.ctx = canvas.getContext("2d");
  sigPad.ctx.lineWidth = 2.5;
  sigPad.ctx.lineCap = "round";
  sigPad.ctx.strokeStyle = "#1c2029";
  sigClear();

  canvas.addEventListener("pointerdown", (e) => {
    sigPad.drawing = true;
    sigPad.hasContent = true;
    const p = sigCanvasPoint(e);
    sigPad.ctx.beginPath();
    sigPad.ctx.moveTo(p.x, p.y);
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!sigPad.drawing) return;
    const p = sigCanvasPoint(e);
    sigPad.ctx.lineTo(p.x, p.y);
    sigPad.ctx.stroke();
  });
  const stop = () => { sigPad.drawing = false; };
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointerleave", stop);

  el("sig-clear-btn").addEventListener("click", sigClear);
  el("sig-cancel-btn").addEventListener("click", closeSignatureModal);
  el("sig-save-btn").addEventListener("click", () => {
    const result = {
      name: el("sig-name").value.trim() || null,
      position: el("sig-position").value.trim() || null,
      signed_date: el("sig-date").value || null,
      signature: el("sig-typed").value.trim() || null,
      signature_image: sigPad.hasContent ? canvas.toDataURL("image/png") : null,
    };
    const callback = sigPad.onSave;
    closeSignatureModal();
    if (callback) callback(result);
  });
}

function openSignatureModal({ title, initial, onSave }) {
  initial = initial || {};
  el("signature-modal-title").textContent = title || "Sign";
  el("sig-name").value = initial.name || "";
  el("sig-position").value = initial.position || "";
  el("sig-date").value = initial.signed_date || todayIso();
  el("sig-typed").value = initial.signature || "";
  sigPad.onSave = onSave;

  sigClear();
  if (initial.signature_image) {
    const img = new Image();
    img.onload = () => {
      sigPad.ctx.drawImage(img, 0, 0, sigPad.canvas.width, sigPad.canvas.height);
      sigPad.hasContent = true;
    };
    img.src = initial.signature_image;
  }

  el("signature-modal").hidden = false;
}

function closeSignatureModal() {
  el("signature-modal").hidden = true;
  sigPad.onSave = null;
}

// ---------------------------------------------------------------------
// New report form
// ---------------------------------------------------------------------

function addPersonRow(prefill) {
  prefill = prefill || {};
  const idx = state.personRowCount++;
  const wrap = document.createElement("div");
  wrap.className = "person-row";
  wrap.dataset.rowIndex = idx;
  wrap.innerHTML = `
    <label>Name<input type="text" class="p-name" maxlength="200" value="${escapeHtml(prefill.name)}"></label>
    <label>Role<input type="text" class="p-designation" maxlength="200" value="${escapeHtml(prefill.designation)}"></label>
    <label>Company<input type="text" class="p-company" maxlength="200" value="${escapeHtml(prefill.company)}"></label>
    <label>ID / Labour card<input type="text" class="p-id" maxlength="100" value="${escapeHtml(prefill.id_number)}"></label>
    <label>Nationality<input type="text" class="p-nationality" maxlength="100" value="${escapeHtml(prefill.nationality)}"></label>
    <label>Contact no.<input type="text" class="p-contact" maxlength="50" value="${escapeHtml(prefill.contact_no)}"></label>
    <button type="button" class="btn btn-danger btn-sm remove-row-btn">✕</button>
  `;
  wrap.querySelector(".remove-row-btn").addEventListener("click", () => wrap.remove());
  el("person-rows").appendChild(wrap);
}

function collectPersonRows() {
  return Array.from(el("person-rows").querySelectorAll(".person-row"))
    .map((row) => ({
      name: row.querySelector(".p-name").value.trim(),
      designation: row.querySelector(".p-designation").value.trim() || null,
      company: row.querySelector(".p-company").value.trim() || null,
      id_number: row.querySelector(".p-id").value.trim() || null,
      nationality: row.querySelector(".p-nationality").value.trim() || null,
      contact_no: row.querySelector(".p-contact").value.trim() || null,
    }))
    .filter((p) => p.name);
}

let niPreparedSignatureImage = null;

function resetNewIncidentForm() {
  el("new-incident-form").reset();
  el("person-rows").innerHTML = "";
  state.personRowCount = 0;
  addPersonRow();
  el("ni-prepared-date").value = todayIso();
  niPreparedSignatureImage = null;
  el("ni-prepared-signature-preview").hidden = true;
  el("ni-prepared-signature-preview").src = "";
}

function openPreparedSignaturePad() {
  openSignatureModal({
    title: "Prepared By signature",
    initial: {
      name: el("ni-prepared-name").value,
      position: el("ni-prepared-position").value,
      signed_date: el("ni-prepared-date").value,
      signature: el("ni-prepared-signature").value,
      signature_image: niPreparedSignatureImage,
    },
    onSave: (result) => {
      el("ni-prepared-name").value = result.name || "";
      el("ni-prepared-position").value = result.position || "";
      if (result.signed_date) el("ni-prepared-date").value = result.signed_date;
      el("ni-prepared-signature").value = result.signature || "";
      niPreparedSignatureImage = result.signature_image;
      const preview = el("ni-prepared-signature-preview");
      if (niPreparedSignatureImage) {
        preview.src = niPreparedSignatureImage;
        preview.hidden = false;
      } else {
        preview.hidden = true;
      }
    },
  });
}

async function submitNewIncident(evt) {
  evt.preventDefault();

  const prepared = {
    name: el("ni-prepared-name").value.trim(),
    position: el("ni-prepared-position").value.trim(),
    signed_date: el("ni-prepared-date").value,
    signature: el("ni-prepared-signature").value.trim(),
    signature_image: niPreparedSignatureImage,
  };
  const hasPrepared = Object.values(prepared).some(Boolean);

  const payload = {
    site_location: el("ni-site-location").value.trim(),
    department_area: el("ni-department-area").value.trim() || null,
    report_date: el("ni-report-date").value,
    report_time: el("ni-report-time").value,
    reported_by: el("ni-reported-by").value.trim(),
    reported_by_job_title: el("ni-reported-by-job-title").value.trim() || null,
    reported_by_id_no: el("ni-reported-by-id-no").value.trim() || null,
    reported_via: el("ni-reported-via").value,
    reported_via_other: el("ni-reported-via-other").value.trim() || null,
    nature_of_report: el("ni-nature-of-report").value,
    nature_of_report_other: el("ni-nature-of-report-other").value.trim() || null,

    involved_persons: collectPersonRows(),
    witnesses: el("ni-witnesses").value.trim() || null,

    incident_type_summary: el("ni-incident-type-summary").value.trim() || null,
    exact_location: el("ni-exact-location").value.trim(),
    incident_date: el("ni-incident-date").value,
    incident_time: el("ni-incident-time").value,

    incident_categories: getCheckedValues(el("ni-categories")),
    incident_category_other: el("ni-category-other").value.trim() || null,

    incident_background: el("ni-background").value.trim(),
    immediate_action_taken: el("ni-action").value.trim() || null,
    root_cause: el("ni-root-cause").value.trim() || null,
    recommendations: el("ni-recommendations").value.trim() || null,
    local_authorities_involvement: el("ni-authorities").value.trim() || null,

    supporting_documents: getCheckedValues(el("ni-supporting-documents")),
    supporting_documents_other: el("ni-supporting-documents-other").value.trim() || null,

    prepared_by: hasPrepared
      ? {
          name: prepared.name || null,
          position: prepared.position || null,
          signed_date: prepared.signed_date || null,
          signature: prepared.signature || null,
          signature_image: prepared.signature_image || null,
        }
      : null,
  };

  try {
    const created = await apiJson("/incidents", { method: "POST", body: JSON.stringify(payload) });
    showToast("Incident reported.");
    resetNewIncidentForm();
    showTab("queue");
    state.offset = 0;
    await loadIncidents();
    selectIncident(created.id);
  } catch (err) {
    showToast(`Failed to report incident: ${err.message}`, true);
  }
}

// ---------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------

function buildAnalyticsQuery() {
  const params = new URLSearchParams();
  params.set("period", el("an-period").value);
  if (el("an-from").value) params.set("from_date", el("an-from").value);
  if (el("an-to").value) params.set("to_date", el("an-to").value);
  return params.toString();
}

async function loadAnalytics() {
  try {
    const data = await apiJson(`/analytics?${buildAnalyticsQuery()}`);
    renderAnalytics(data);
  } catch (err) {
    showToast(`Failed to load analytics: ${err.message}`, true);
  }
}

function renderStatCards(data) {
  const byStatus = Object.fromEntries((data.status_breakdown || []).map((s) => [s.key, s.count]));
  const cards = [
    { label: "Total incidents", value: data.total_incidents, cls: "" },
    { label: "Reported", value: byStatus.reported || 0, cls: "accent" },
    { label: "Under review", value: byStatus.under_review || 0, cls: "" },
    { label: "Closed", value: byStatus.closed || 0, cls: "" },
    { label: "People appearing more than once", value: data.repeat_involved_persons.length, cls: "danger" },
    { label: "Recurring site/type patterns", value: data.repeat_site_category_patterns.length, cls: "danger" },
  ];
  el("an-stat-cards").innerHTML = cards
    .map((c) => `<div class="stat-card ${c.cls}"><div class="value">${c.value}</div><div class="label">${c.label}</div></div>`)
    .join("");
}

function renderTrendChart(trend) {
  const container = el("an-trend-chart");
  container.innerHTML = "";
  if (!trend.length) {
    container.innerHTML = '<span class="hint">No incidents in this range.</span>';
    return;
  }
  const max = Math.max(...trend.map((t) => t.count), 1);
  trend.forEach((t) => {
    const wrap = document.createElement("div");
    wrap.className = "trend-bar-wrap";
    const heightPct = Math.max((t.count / max) * 100, 4);
    wrap.innerHTML = `
      <span class="trend-count">${t.count}</span>
      <div class="trend-bar" style="height:${heightPct}%;"></div>
      <span class="trend-label">${escapeHtml(t.period)}</span>
    `;
    container.appendChild(wrap);
  });
}

function renderBarList(containerId, items, { labelFormatter } = {}) {
  const container = el(containerId);
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = '<span class="hint">No data yet.</span>';
    return;
  }
  const max = Math.max(...items.map((i) => i.count), 1);
  items.forEach((item) => {
    const label = labelFormatter ? labelFormatter(item.key) : humanize(item.key);
    const pct = Math.max((item.count / max) * 100, 4);
    const row = document.createElement("div");
    row.className = "bar-list-row";
    row.innerHTML = `
      <span class="bar-list-label" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
      <span class="bar-list-track"><span class="bar-list-fill" style="width:${pct}%;"></span></span>
      <span class="bar-list-count">${item.count}</span>
    `;
    container.appendChild(row);
  });
}

function renderAnalytics(data) {
  renderStatCards(data);
  renderTrendChart(data.trend);
  renderBarList("an-category-bars", data.category_breakdown, { labelFormatter: (k) => CATEGORY_LABELS[k] || humanize(k) });
  renderBarList("an-nature-bars", data.nature_breakdown);
  renderBarList("an-site-bars", data.top_sites, { labelFormatter: (k) => k });
  renderBarList("an-reporter-bars", data.top_reporters, { labelFormatter: (k) => k });
  renderBarList("an-reviewer-bars", data.top_reviewers, { labelFormatter: (k) => k });
  renderBarList("an-approver-bars", data.top_approvers, { labelFormatter: (k) => k });

  const peopleBody = el("an-repeat-people-rows");
  peopleBody.innerHTML = "";
  if (data.repeat_involved_persons.length === 0) {
    peopleBody.innerHTML = '<tr><td colspan="4" class="hint">No one appears in more than one incident in this range.</td></tr>';
  } else {
    data.repeat_involved_persons.forEach((p) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td data-label="Name">${escapeHtml(p.name)}</td>
        <td data-label="ID / labour card">${escapeHtml(p.id_number) || "—"}</td>
        <td data-label="Occurrences"><span class="repeat-count-badge">${p.count}×</span></td>
        <td data-label="Last seen">${escapeHtml(p.last_seen) || "—"}</td>
      `;
      peopleBody.appendChild(tr);
    });
  }

  const patternsBody = el("an-repeat-patterns-rows");
  patternsBody.innerHTML = "";
  if (data.repeat_site_category_patterns.length === 0) {
    patternsBody.innerHTML = '<tr><td colspan="3" class="hint">No recurring site/type patterns in this range.</td></tr>';
  } else {
    data.repeat_site_category_patterns.forEach((p) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td data-label="Site / location">${escapeHtml(p.site_location)}</td>
        <td data-label="Incident type">${escapeHtml(CATEGORY_LABELS[p.category] || humanize(p.category))}</td>
        <td data-label="Occurrences"><span class="repeat-count-badge">${p.count}×</span></td>
      `;
      patternsBody.appendChild(tr);
    });
  }
}

// ---------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------

function wireEvents() {
  el("tab-queue").addEventListener("click", () => showTab("queue"));
  el("tab-new").addEventListener("click", () => showTab("new"));
  el("cancel-new-incident").addEventListener("click", () => showTab("queue"));

  let searchDebounce;
  el("filter-search").addEventListener("input", (e) => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      state.filters.search = e.target.value.trim();
      state.offset = 0;
      loadIncidents();
    }, 300);
  });
  let siteDebounce;
  el("filter-site").addEventListener("input", (e) => {
    clearTimeout(siteDebounce);
    siteDebounce = setTimeout(() => {
      state.filters.site_location = e.target.value.trim();
      state.offset = 0;
      loadIncidents();
    }, 300);
  });
  el("filter-status").addEventListener("change", (e) => {
    state.filters.status = e.target.value;
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

  el("save-status-btn").addEventListener("click", saveStatus);
  el("comment-form").addEventListener("submit", addComment);
  el("delete-incident-btn").addEventListener("click", deleteIncidentHandler);
  el("upload-picture-btn").addEventListener("click", () => uploadAttachment("picture"));
  el("upload-document-btn").addEventListener("click", () => uploadAttachment("document"));

  el("add-person-row-btn").addEventListener("click", () => addPersonRow());
  el("new-incident-form").addEventListener("submit", submitNewIncident);
  el("ni-prepared-sign-btn").addEventListener("click", openPreparedSignaturePad);

  el("tab-analytics").addEventListener("click", () => showTab("analytics"));
  el("an-refresh-btn").addEventListener("click", loadAnalytics);
}

function initSelects() {
  populateSelect(el("filter-status"), STATUSES, { includeEmpty: true, emptyLabel: "All statuses" });
  populateSelect(el("filter-category"), INCIDENT_CATEGORIES.map((c) => c[0]), {
    includeEmpty: true,
    emptyLabel: "All incident types",
    labels: CATEGORY_LABELS,
  });
  populateSelect(el("update-status"), STATUSES);

  populateSelect(el("ni-reported-via"), REPORTED_VIA);
  populateSelect(el("ni-nature-of-report"), NATURE_OF_REPORT);
  buildCheckboxGrid(el("ni-categories"), INCIDENT_CATEGORIES, "category");
  buildCheckboxGrid(el("ni-supporting-documents"), SUPPORTING_DOCUMENTS, "supporting");
}

initSelects();
wireEvents();
initSignaturePad();
resetNewIncidentForm();
loadIncidents();
