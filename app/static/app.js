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

// Mirrors app/auth_models.py's Role/ROLE_LEVEL and app/routers/incidents.py's
// _SIGNOFF_MIN_ROLE — kept in sync by hand since this is a static frontend
// with no shared build step. The backend is the actual enforcement point;
// this only drives which controls the UI shows as usable.
const ROLES = ["security_officer", "security_supervisor", "management", "admin"];
const ROLE_LEVEL = { security_officer: 1, security_supervisor: 2, management: 3, admin: 4 };
const SIGNOFF_MIN_ROLE = { reviewed_by: "security_supervisor", approved_by: "management" };

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------
// Auth state
// ---------------------------------------------------------------------

const AUTH_STORAGE_KEY = "sp_auth";
const auth = { token: null, refreshToken: null, user: null };

function loadStoredAuth() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && parsed.token && parsed.refreshToken && parsed.user) {
      auth.token = parsed.token;
      auth.refreshToken = parsed.refreshToken;
      auth.user = parsed.user;
    }
  } catch (_) {
    // corrupted or inaccessible storage — just start logged out
  }
}

function persistAuth() {
  try {
    if (auth.token && auth.refreshToken && auth.user) {
      localStorage.setItem(
        AUTH_STORAGE_KEY,
        JSON.stringify({ token: auth.token, refreshToken: auth.refreshToken, user: auth.user })
      );
    } else {
      localStorage.removeItem(AUTH_STORAGE_KEY);
    }
  } catch (_) {
    // private browsing / storage disabled — session just won't survive a reload
  }
}

// Access tokens are short-lived by design (ACCESS_TOKEN_EXPIRE_MINUTES) —
// this exchanges the longer-lived refresh token for a new one the moment
// a request comes back 401, so a session doesn't just die mid-use. Several
// 401s arriving at once (e.g. a burst of parallel requests right as the
// token expires) share one in-flight refresh instead of each firing their
// own — the API would treat that as fine, but there's no reason to.
let refreshPromise = null;

async function refreshAccessToken() {
  if (!auth.refreshToken) return false;
  if (!refreshPromise) {
    refreshPromise = fetch("/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: auth.refreshToken }),
    })
      .then((resp) => (resp.ok ? resp.json() : null))
      .catch(() => null)
      .finally(() => {
        refreshPromise = null;
      });
  }
  const result = await refreshPromise;
  if (!result) return false;
  auth.token = result.access_token;
  persistAuth();
  return true;
}

function hasMinRole(role) {
  return !!auth.user && ROLE_LEVEL[auth.user.role] >= ROLE_LEVEL[role];
}

function isAdmin() {
  return hasMinRole("admin");
}

// Management can provision/manage accounts and sites too, not just Admin —
// see app/routers/users.py and app/routers/sites.py. Kept as its own
// helper (rather than inlining hasMinRole("management") everywhere) so the
// "who can reach the Users/Sites tabs" question reads as one concept.
function canManageAccounts() {
  return hasMinRole("management");
}

// Mirrors app/routers/users.py's _assert_can_manage_role: Admin can act on
// anyone; Management can only act on accounts below its own level
// (Security Officer, Security Supervisor) — never a peer Management
// account or an Admin account. Used to grey out controls the backend
// would reject anyway, so a Management user doesn't hit a surprise 403.
function canManageUserRow(targetRole) {
  return isAdmin() || ROLE_LEVEL[targetRole] < ROLE_LEVEL["management"];
}

function assignableRoles() {
  return isAdmin() ? ROLES : ROLES.filter((r) => ROLE_LEVEL[r] < ROLE_LEVEL["management"]);
}

const state = {
  offset: 0,
  total: 0,
  items: [],
  selectedId: null,
  filters: { status: "", category: "", site_location: "", incident_number: "", search: "" },
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

async function api(path, options = {}, { skipAuthRedirect = false, _retried = false } = {}) {
  const headers = { ...(options.headers || {}) };
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;

  const resp = await fetch(path, { ...options, headers });

  // The access token expired (the common case, every
  // ACCESS_TOKEN_EXPIRE_MINUTES) — try exchanging the refresh token for a
  // new one and replay this request exactly once before giving up. Tried
  // unconditionally (even under skipAuthRedirect, e.g. bootstrapAuth's
  // /auth/me check) since a successful refresh always resolves things
  // better than whatever that caller would otherwise do with a 401.
  if (resp.status === 401 && !_retried && (await refreshAccessToken())) {
    return api(path, options, { skipAuthRedirect, _retried: true });
  }

  if (resp.status === 401 && !skipAuthRedirect) {
    // Refreshing didn't help either — invalid/expired refresh token, no
    // refresh token stored, or the account's been deactivated/revoked
    // since. There's no recovering from this without logging in again.
    logout();
    showToast("Your session has ended — please log in again.", true);
    throw new Error("Session expired");
  }

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

function apiJson(path, options = {}, config) {
  return api(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } }, config);
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
// Login / logout
// ---------------------------------------------------------------------

function showLoginScreen() {
  el("login-screen").hidden = false;
  el("app-shell").hidden = true;
}

async function showApp() {
  el("login-screen").hidden = true;
  el("app-shell").hidden = false;
  el("current-user-label").textContent = `${auth.user.full_name} · ${humanize(auth.user.role)}`;
  el("tab-users").hidden = !canManageAccounts();
  el("tab-sites").hidden = !canManageAccounts();
  await loadActiveSitesForForm();
  resetNewIncidentForm(); // now that auth.user is known, prefill "prepared by"
  showTab("queue");
  loadIncidents();
}

async function handleLogin(evt) {
  evt.preventDefault();
  el("login-error").hidden = true;
  const email = el("login-email").value.trim();
  const password = el("login-password").value;
  try {
    const data = await apiJson("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }, { skipAuthRedirect: true });
    auth.token = data.access_token;
    auth.refreshToken = data.refresh_token;
    auth.user = data.user;
    persistAuth();
    el("login-form").reset();
    showApp();
  } catch (err) {
    el("login-error").textContent = err.message || "Login failed.";
    el("login-error").hidden = false;
  }
}

function logout() {
  auth.token = null;
  auth.refreshToken = null;
  auth.user = null;
  persistAuth();
  showLoginScreen();
}

async function logoutEverywhere() {
  if (!confirm("Log out of every device using your account? You'll need to log in again here too.")) return;
  try {
    await api("/auth/logout-everywhere", { method: "POST" });
  } catch (_) {
    // Even if the request itself failed to round-trip, there's nothing
    // useful left to do with the current (now-untrusted) tokens — clear
    // them locally either way.
  }
  logout();
  showToast("Logged out everywhere. Log in again to continue.");
}

async function bootstrapAuth() {
  loadStoredAuth();
  if (!auth.token) {
    showLoginScreen();
    return;
  }
  try {
    // Validate the stored token still works (not expired — or, if it is,
    // silently refreshed via the api() layer — account not since-disabled
    // or had every session revoked) before trusting the cached profile.
    auth.user = await apiJson("/auth/me", {}, { skipAuthRedirect: true });
    persistAuth();
    showApp();
  } catch (_) {
    auth.token = null;
    auth.refreshToken = null;
    auth.user = null;
    persistAuth();
    showLoginScreen();
  }
}

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------

function showTab(tab) {
  el("view-queue").hidden = tab !== "queue";
  el("view-new").hidden = tab !== "new";
  el("view-analytics").hidden = tab !== "analytics";
  el("view-users").hidden = tab !== "users";
  el("view-sites").hidden = tab !== "sites";
  el("tab-queue").classList.toggle("active", tab === "queue");
  el("tab-new").classList.toggle("active", tab === "new");
  el("tab-analytics").classList.toggle("active", tab === "analytics");
  el("tab-users").classList.toggle("active", tab === "users");
  el("tab-sites").classList.toggle("active", tab === "sites");
  if (tab === "analytics") loadAnalytics();
  if (tab === "users") loadUsers();
  if (tab === "sites") loadSites();
  if (tab === "new") loadActiveSitesForForm();
}

// ---------------------------------------------------------------------
// Queue list
// ---------------------------------------------------------------------

function buildQuery() {
  const params = new URLSearchParams();
  if (state.filters.status) params.set("status", state.filters.status);
  if (state.filters.category) params.set("category", state.filters.category);
  if (state.filters.site_location) params.set("site_location", state.filters.site_location);
  if (state.filters.incident_number) params.set("incident_number", state.filters.incident_number);
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
        <td data-label="Incident #"><span class="row-title">${escapeHtml(incident.incident_number)}</span></td>
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

// Binary downloads (attachments, PDF export) go through plain fetch rather
// than api()/apiJson(), since the response is a blob, not JSON — but they
// still benefit from the same silent-refresh-on-401 as everything else.
async function authFetch(path) {
  let resp = await fetch(path, { headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {} });
  if (resp.status === 401 && (await refreshAccessToken())) {
    resp = await fetch(path, { headers: { Authorization: `Bearer ${auth.token}` } });
  }
  return resp;
}

async function openAttachment(incidentId, attachmentId, filename) {
  // Attachments require the same auth as everything else now, so a plain
  // <a href> won't carry the Authorization header — fetch it ourselves and
  // open the resulting blob instead.
  try {
    const resp = await authFetch(`/incidents/${incidentId}/attachments/${attachmentId}`);
    if (resp.status === 401) {
      logout();
      showToast("Your session has ended — please log in again.", true);
      return;
    }
    if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (err) {
    showToast(`Failed to open ${filename}: ${err.message}`, true);
  }
}

async function downloadIncidentPdf(incidentId) {
  try {
    const resp = await authFetch(`/incidents/${incidentId}/pdf`);
    if (resp.status === 401) {
      logout();
      showToast("Your session has ended — please log in again.", true);
      return;
    }
    if (!resp.ok) throw new Error(`Request failed (${resp.status})`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (err) {
    showToast(`Failed to generate PDF: ${err.message}`, true);
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
      <a href="#" title="${escapeHtml(a.filename)}">${escapeHtml(a.filename)}</a>
      <button type="button" title="Remove" data-attachment-id="${a.id}">✕</button>
    `;
    chip.querySelector("a").addEventListener("click", (e) => {
      e.preventDefault();
      openAttachment(incidentId, a.id, a.filename);
    });
    chip.querySelector("button").addEventListener("click", () => deleteAttachment(incidentId, a.id));
    container.appendChild(chip);
  }
}

function renderApprovals(incident) {
  const grid = el("approvals-grid");
  grid.innerHTML = "";

  for (const [field, label, roleLabel] of APPROVAL_FIELDS) {
    const value = incident[field];
    const minRole = SIGNOFF_MIN_ROLE[field]; // undefined for prepared_by — anyone may (re-)prepare
    const canSign = !minRole || hasMinRole(minRole);
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
        <button type="button" class="btn btn-sm" data-sign="${field}" ${canSign ? "" : "disabled"}
          title="${canSign ? "" : `Requires the ${humanize(minRole)} role or higher`}">${value ? "Edit sign-off" : "Sign"}</button>
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
  // reviewed_by/approved_by are role-gated: the server always overwrites
  // name/position with whoever is logged in, so let the modal reflect that
  // instead of implying a typed name here would matter.
  const isRoleGated = !!SIGNOFF_MIN_ROLE[field];
  openSignatureModal({
    title: `${label} (${roleLabel})`,
    initial: isRoleGated ? { ...existing, name: auth.user.full_name, position: existing.position || null } : existing,
    lockName: isRoleGated,
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
  el("delete-incident-btn").hidden = !isAdmin();

  el("detail-title").textContent = incident.site_location;
  el("detail-number").textContent = incident.incident_number;
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
  el("sig-use-saved-btn").addEventListener("click", () => {
    if (auth.user && auth.user.saved_signature_image) drawSignatureImage(auth.user.saved_signature_image);
  });
  el("sig-cancel-btn").addEventListener("click", closeSignatureModal);
  el("sig-save-btn").addEventListener("click", async () => {
    const result = {
      name: el("sig-name").value.trim() || null,
      position: el("sig-position").value.trim() || null,
      signed_date: el("sig-date").value || null,
      signature: el("sig-typed").value.trim() || null,
      signature_image: sigPad.hasContent ? canvas.toDataURL("image/png") : null,
    };
    if (el("sig-save-as-mine").checked) {
      // Best-effort: this is a convenience on top of the actual sign-off,
      // so a failure here shouldn't block the sign-off itself from saving.
      try {
        const updated = await apiJson("/auth/me/signature", {
          method: "PUT",
          body: JSON.stringify({ signature_image: result.signature_image, signature: result.signature }),
        });
        auth.user = updated;
        persistAuth();
      } catch (err) {
        showToast(`Saved the sign-off, but couldn't save it as your signature: ${err.message}`, true);
      }
    }
    const callback = sigPad.onSave;
    closeSignatureModal();
    if (callback) callback(result);
  });
}

function drawSignatureImage(dataUri) {
  const img = new Image();
  img.onload = () => {
    sigPad.ctx.clearRect(0, 0, sigPad.canvas.width, sigPad.canvas.height);
    sigPad.ctx.fillStyle = "#ffffff";
    sigPad.ctx.fillRect(0, 0, sigPad.canvas.width, sigPad.canvas.height);
    sigPad.ctx.drawImage(img, 0, 0, sigPad.canvas.width, sigPad.canvas.height);
    sigPad.hasContent = true;
  };
  img.src = dataUri;
}

function openSignatureModal({ title, initial, onSave, lockName = false }) {
  initial = initial || {};
  el("signature-modal-title").textContent = title || "Sign";
  el("sig-name").value = initial.name || "";
  el("sig-name").readOnly = lockName;
  el("sig-name-wrap").style.opacity = lockName ? "0.7" : "1";
  el("signature-modal-note").textContent = lockName
    ? `Signing as ${initial.name} — this identity comes from your login and can't be changed here.`
    : "Draw your signature below, or leave it blank and just type your name.";
  el("sig-position").value = initial.position || "";
  el("sig-date").value = initial.signed_date || todayIso();
  el("sig-save-as-mine").checked = false;

  const savedImage = auth.user && auth.user.saved_signature_image;
  el("sig-use-saved-btn").hidden = !savedImage;

  // Reuse this person's saved signature as a starting point whenever this
  // sign-off doesn't already have its own drawn image — that's the whole
  // point of saving one. Editing an existing sign-off (initial.signature_image
  // set) still shows what's actually on it; "Use my saved signature" is
  // there to switch to the saved one instead, if they want.
  el("sig-typed").value = initial.signature || (!initial.signature_image && auth.user && auth.user.saved_signature_text) || "";
  sigPad.onSave = onSave;

  sigClear();
  if (initial.signature_image) {
    drawSignatureImage(initial.signature_image);
  } else if (savedImage) {
    drawSignatureImage(savedImage);
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
  // Convenience default — whoever is filing the report is filling this
  // form in, so start "prepared by" as them; still freely editable.
  if (auth.user) {
    el("ni-prepared-name").value = auth.user.full_name;
    el("ni-prepared-position").value = humanize(auth.user.role);
  }
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

  const chosenSiteId = el("ni-site").value;
  const payload = {
    site_id: chosenSiteId || null,
    site_other: chosenSiteId ? null : el("ni-site-other").value.trim() || null,
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
// Sites registry — the New Report form's dropdown, plus CRUD for
// Admin/Management (see canManageAccounts)
// ---------------------------------------------------------------------

function populateSiteSelect(select, sites) {
  select.innerHTML = "";
  for (const s of sites) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.name} (${s.code})`;
    select.appendChild(opt);
  }
  const other = document.createElement("option");
  other.value = "";
  other.textContent = "Other (specify site name)";
  select.appendChild(other);
}

async function loadActiveSitesForForm() {
  try {
    const sites = await apiJson("/sites");
    populateSiteSelect(el("ni-site"), sites);
  } catch (err) {
    showToast(`Failed to load sites: ${err.message}`, true);
  }
}

let sitesCache = [];

async function loadSites() {
  try {
    sitesCache = await apiJson("/sites?include_inactive=true");
    renderSites();
  } catch (err) {
    showToast(`Failed to load sites: ${err.message}`, true);
  }
}

function renderSites() {
  el("sites-count").textContent = sitesCache.length;
  const tbody = el("sites-rows");
  tbody.innerHTML = "";

  sitesCache.forEach((s) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="Code">${escapeHtml(s.code)}</td>
      <td data-label="Name">${escapeHtml(s.name)}</td>
      <td data-label="Status">
        <label style="flex-direction:row;align-items:center;gap:0.4rem;margin:0;">
          <input type="checkbox" class="s-active" ${s.is_active ? "checked" : ""} style="width:auto;">
          Active
        </label>
      </td>
      <td data-label="">
        <button type="button" class="btn btn-danger btn-sm s-delete">Delete</button>
      </td>
    `;
    tr.querySelector(".s-active").addEventListener("change", (e) => updateSite(s.id, { is_active: e.target.checked }));
    tr.querySelector(".s-delete").addEventListener("click", () => deleteSite(s.id, s.name));
    tbody.appendChild(tr);
  });
}

async function updateSite(siteId, patch) {
  try {
    await apiJson(`/sites/${siteId}`, { method: "PATCH", body: JSON.stringify(patch) });
    showToast("Site updated.");
    loadSites();
    loadActiveSitesForForm();
  } catch (err) {
    showToast(`Update failed: ${err.message}`, true);
    loadSites(); // revert any optimistic UI (e.g. a toggled checkbox)
  }
}

async function deleteSite(siteId, name) {
  if (!confirm(`Delete the site "${name}"? Only possible if no incident references it yet.`)) return;
  try {
    await api(`/sites/${siteId}`, { method: "DELETE" });
    showToast("Site deleted.");
    loadSites();
    loadActiveSitesForForm();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, true);
  }
}

async function submitNewSite(evt) {
  evt.preventDefault();
  const payload = { code: el("ns-code").value.trim(), name: el("ns-name").value.trim() };
  try {
    await apiJson("/sites", { method: "POST", body: JSON.stringify(payload) });
    showToast(`Site "${payload.name}" added.`);
    el("new-site-form").reset();
    loadSites();
    loadActiveSitesForForm();
  } catch (err) {
    showToast(`Failed to add site: ${err.message}`, true);
  }
}

// ---------------------------------------------------------------------
// Users (Admin and Management — see canManageAccounts/canManageUserRow)
// ---------------------------------------------------------------------

let usersCache = [];

async function loadUsers() {
  try {
    usersCache = await apiJson("/users");
    renderUsers();
    // Re-populate each time: a Management viewer only gets to assign roles
    // below its own level, an Admin viewer gets all of them.
    populateSelect(el("nu-role"), assignableRoles());
    el("nu-role").value = "security_officer";
  } catch (err) {
    showToast(`Failed to load accounts: ${err.message}`, true);
  }
}

function renderUsers() {
  el("users-count").textContent = usersCache.length;
  const tbody = el("users-rows");
  tbody.innerHTML = "";

  usersCache.forEach((u) => {
    const isSelf = u.id === auth.user.id;
    // A Management viewer can't touch a peer Management or Admin account
    // at all (mirrors the backend's _assert_can_manage_role) — grey out
    // every control on that row rather than let them hit a 403.
    const manageable = canManageUserRow(u.role);
    const locked = isSelf || !manageable;
    const tr = document.createElement("tr");
    tr.title = !manageable ? "Only an Admin can manage this account." : "";
    tr.innerHTML = `
      <td data-label="Name">${escapeHtml(u.full_name)}</td>
      <td data-label="Email">${escapeHtml(u.email)}</td>
      <td data-label="Role"></td>
      <td data-label="Status">
        <label style="flex-direction:row;align-items:center;gap:0.4rem;margin:0;">
          <input type="checkbox" class="u-active" ${u.is_active ? "checked" : ""} ${locked ? "disabled" : ""} style="width:auto;">
          Active
        </label>
      </td>
      <td data-label="">
        <button type="button" class="btn btn-sm u-revoke" ${locked ? "disabled" : ""} title="End every session this person is currently logged in on, without deactivating the account">Force logout</button>
        <button type="button" class="btn btn-danger btn-sm u-delete" ${locked ? "disabled" : ""}>Delete</button>
      </td>
    `;

    const roleSelect = document.createElement("select");
    roleSelect.className = "role-select-inline u-role";
    // Always include this row's current role even if it's above what the
    // viewer could newly assign, so e.g. a Management viewer still sees
    // "admin" on an Admin's row instead of it silently falling back to the
    // first option.
    populateSelect(roleSelect, Array.from(new Set([...assignableRoles(), u.role])));
    roleSelect.value = u.role;
    if (locked) roleSelect.disabled = true;
    tr.querySelector('[data-label="Role"]').appendChild(roleSelect);

    roleSelect.addEventListener("change", () => updateUser(u.id, { role: roleSelect.value }));
    tr.querySelector(".u-active").addEventListener("change", (e) => updateUser(u.id, { is_active: e.target.checked }));
    tr.querySelector(".u-delete").addEventListener("click", () => deleteUser(u.id, u.full_name));
    tr.querySelector(".u-revoke").addEventListener("click", () => revokeUserSessions(u.id, u.full_name));

    tbody.appendChild(tr);
  });
}

async function updateUser(userId, patch) {
  try {
    await apiJson(`/users/${userId}`, { method: "PATCH", body: JSON.stringify(patch) });
    showToast("Account updated.");
    loadUsers();
  } catch (err) {
    showToast(`Update failed: ${err.message}`, true);
    loadUsers(); // revert any optimistic UI (e.g. a toggled checkbox)
  }
}

async function deleteUser(userId, fullName) {
  if (!confirm(`Delete the account for ${fullName}? This cannot be undone.`)) return;
  try {
    await api(`/users/${userId}`, { method: "DELETE" });
    showToast("Account deleted.");
    loadUsers();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, true);
  }
}

async function revokeUserSessions(userId, fullName) {
  if (!confirm(`End every active session for ${fullName}? Their account stays enabled — they'll just need to log in again.`)) return;
  try {
    await api(`/users/${userId}/revoke-sessions`, { method: "POST" });
    showToast(`${fullName}'s sessions were ended.`);
  } catch (err) {
    showToast(`Failed to end sessions: ${err.message}`, true);
  }
}

async function submitNewUser(evt) {
  evt.preventDefault();
  const payload = {
    full_name: el("nu-full-name").value.trim(),
    email: el("nu-email").value.trim(),
    role: el("nu-role").value,
    password: el("nu-password").value,
  };
  try {
    await apiJson("/users", { method: "POST", body: JSON.stringify(payload) });
    showToast(`Account created for ${payload.full_name}.`);
    el("new-user-form").reset();
    el("nu-role").value = "security_officer";
    loadUsers();
  } catch (err) {
    showToast(`Failed to create account: ${err.message}`, true);
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
  let numberDebounce;
  el("filter-number").addEventListener("input", (e) => {
    clearTimeout(numberDebounce);
    numberDebounce = setTimeout(() => {
      state.filters.incident_number = e.target.value.trim();
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
  el("download-pdf-btn").addEventListener("click", () => downloadIncidentPdf(el("detail-content").dataset.id));
  el("delete-incident-btn").addEventListener("click", deleteIncidentHandler);
  el("upload-picture-btn").addEventListener("click", () => uploadAttachment("picture"));
  el("upload-document-btn").addEventListener("click", () => uploadAttachment("document"));

  el("add-person-row-btn").addEventListener("click", () => addPersonRow());
  el("new-incident-form").addEventListener("submit", submitNewIncident);
  el("ni-prepared-sign-btn").addEventListener("click", openPreparedSignaturePad);

  el("tab-analytics").addEventListener("click", () => showTab("analytics"));
  el("an-refresh-btn").addEventListener("click", loadAnalytics);

  el("tab-users").addEventListener("click", () => showTab("users"));
  el("new-user-form").addEventListener("submit", submitNewUser);

  el("tab-sites").addEventListener("click", () => showTab("sites"));
  el("new-site-form").addEventListener("submit", submitNewSite);

  el("login-form").addEventListener("submit", handleLogin);
  el("logout-btn").addEventListener("click", logout);
  el("logout-everywhere-btn").addEventListener("click", logoutEverywhere);
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

  populateSelect(el("nu-role"), ROLES);
  el("nu-role").value = "security_officer";
}

initSelects();
wireEvents();
initSignaturePad();
resetNewIncidentForm();
bootstrapAuth();
