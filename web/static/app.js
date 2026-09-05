// Shared helpers for the vphone web UI.

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("unauthorized");
  }
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = (body && body.detail) || body || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

const apiJSON = (path, method, data) =>
  api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {}),
  });

async function loadMe() {
  try {
    return await api("/api/me");
  } catch {
    location.href = "/login";
    return null;
  }
}

function qs(name) {
  return new URLSearchParams(location.search).get(name);
}

// ---- SVG Icons (inline, no dependencies) ----
const ICONS = {
  dashboard: `<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>`,
  admin: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  logs: `<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>`,
};

// ---- Detect current page ----
function currentPage() {
  const path = location.pathname;
  if (path === '/' || path === '/index.html') return 'dashboard';
  if (path.startsWith('/vm')) return 'device';
  if (path.startsWith('/admin')) return 'admin';
  if (path.startsWith('/logs')) return 'logs';
  return '';
}

// ---- Build top navigation bar ----
function buildShell(me) {
  const page = currentPage();
  const isAdmin = me && me.role === 'admin';

  const topbarEl = document.getElementById('topbar');
  if (!topbarEl) return;

  // On mobile the nav collapses behind the brand icon (tap to open the menu).
  let html = `
    <button class="brand" id="navToggle" type="button" title="Menu">
      <div class="brand-icon">V</div>
      <span>VPHONE</span>
      <svg class="brand-caret" viewBox="0 0 24 24" width="14" height="14"><polyline points="6 9 12 15 18 9"/></svg>
    </button>
    <div class="nav-links" id="navLinks">
      <a class="nav-link ${page === 'dashboard' ? 'active' : ''}" href="/">
        ${ICONS.dashboard} Dashboard
      </a>`;

  if (isAdmin) {
    html += `
      <div class="nav-divider"></div>
      <a class="nav-link ${page === 'admin' ? 'active' : ''}" href="/admin">
        ${ICONS.admin} Admin
      </a>
      <a class="nav-link ${page === 'logs' ? 'active' : ''}" href="/logs">
        ${ICONS.logs} Logs
      </a>`;
  }

  html += `
    </div>
    <span class="spacer"></span>
    <span class="who">${me ? me.username + ' (' + me.role + ')' : ''}</span>
    <button id="logoutBtn" style="font-size:11px; padding:5px 10px">Logout</button>`;

  topbarEl.innerHTML = html;

  document.getElementById('logoutBtn').onclick = async () => {
    await api("/api/logout", { method: "POST" });
    location.href = "/login";
  };

  // Mobile: tapping the brand icon toggles the collapsed nav menu. (On desktop
  // the menu is always visible and `.nav-open` has no effect — see CSS.)
  const toggle = document.getElementById('navToggle');
  if (toggle) {
    toggle.onclick = (e) => { e.stopPropagation(); topbarEl.classList.toggle('nav-open'); };
    document.addEventListener('click', (e) => {
      if (!topbarEl.contains(e.target)) topbarEl.classList.remove('nav-open');
    });
    const links = document.getElementById('navLinks');
    if (links) links.addEventListener('click', () => topbarEl.classList.remove('nav-open'));
  }
}

// Legacy compat
function topbar(me) {
  buildShell(me);
}
