/**
 * vphone Debug UI Components — Professional Debug Suite
 *
 * Components:
 *   DebugLogViewer        — embedded log viewer with search/filter/auto-scroll
 *   DebugStatsPanel       — FPS, latency, memory, uptime (auto-poll)
 *   DebugCommandInspector — ring-buffer command history
 *   DebugAppManager       — per-app list, launch, filter
 *   DebugCrashViewer      — Apple .ips crash report browser
 *   DebugNetworkInspector — API request/response inspector
 *   DebugSystemPanel      — full system diagnostics + mini charts
 *   highlightLogText      — color-code raw log text
 */

// ---- Log text highlighting ----

const LEVEL_PATTERNS = [
  { re: /\b(error|fail|fatal|panic|exception|traceback|crash)\b/i, cls: 'lvl-error' },
  { re: /\b(warn|warning)\b/i, cls: 'lvl-warn' },
  { re: /\b(debug|trace)\b/i, cls: 'lvl-debug' },
  { re: /\b(info)\b/i, cls: 'lvl-info' },
];

function classifyLevel(line) {
  for (const { re, cls } of LEVEL_PATTERNS) {
    if (re.test(line)) return cls;
  }
  return '';
}

function highlightLogText(text) {
  const lines = text.split('\n');
  return lines.map(line => {
    const cls = classifyLevel(line);
    const escaped = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return cls ? `<span class="${cls}">${escaped}</span>` : escaped;
  }).join('\n');
}

function _escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- DebugLogViewer ----

class DebugLogViewer {
  constructor(container, opts = {}) {
    this.container = container;
    this.vmId = opts.vmId;
    this.autoConnect = opts.autoConnect !== false;
    this.maxHeight = opts.maxHeight || null;  // null = flex fill parent, '400px' = fixed
    this.logName = opts.logName || 'boot';
    this.lines = [];
    this.ws = null;
    this.follow = true;
    this.levelFilter = '';
    this.searchTerm = '';
    this._build();
    if (this.autoConnect) this.connect();
  }

  _build() {
    this.container.innerHTML = '';
    this.container.className = 'log-viewer';

    const tb = document.createElement('div');
    tb.className = 'log-toolbar-mini';

    const logSelect = document.createElement('select');
    logSelect.innerHTML = '<option value="boot">boot.log</option><option value="debug">debug.log</option>';
    logSelect.value = this.logName;
    logSelect.addEventListener('change', () => {
      this.logName = logSelect.value;
      this.lines = [];
      this._renderLines();
      this.disconnect();
      this.connect();
    });

    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search…';
    searchInput.addEventListener('input', () => {
      this.searchTerm = searchInput.value;
      this._renderLines();
    });

    const levelSelect = document.createElement('select');
    levelSelect.innerHTML = '<option value="">All</option><option value="error">Error</option><option value="warn">Warn</option><option value="info">Info</option><option value="debug">Debug</option>';
    levelSelect.addEventListener('change', () => {
      this.levelFilter = levelSelect.value;
      this._renderLines();
    });

    const followBtn = document.createElement('button');
    followBtn.textContent = 'Follow';
    followBtn.className = this.follow ? 'active' : '';
    followBtn.addEventListener('click', () => {
      this.follow = !this.follow;
      followBtn.className = this.follow ? 'active' : '';
      if (this.follow) this._scrollToBottom();
    });

    const clearBtn = document.createElement('button');
    clearBtn.textContent = 'Clear';
    clearBtn.addEventListener('click', () => { this.lines = []; this._renderLines(); });

    tb.appendChild(logSelect);
    tb.appendChild(searchInput);
    tb.appendChild(levelSelect);
    tb.appendChild(followBtn);
    tb.appendChild(clearBtn);

    // Level breakdown bar
    const levelBar = document.createElement('div');
    levelBar.className = 'log-level-bar';
    levelBar.innerHTML = '<span class="ll-err" title="Errors">0</span><span class="ll-warn" title="Warnings">0</span><span class="ll-info" title="Info">0</span><span class="ll-dbg" title="Debug">0</span>';

    const body = document.createElement('div');
    body.className = 'log-body-mini';
    // Use flex: 1 to fill available space; maxHeight only for non-flex contexts
    if (this.maxHeight && this.maxHeight !== '100%') {
      body.style.maxHeight = this.maxHeight;
    } else {
      body.style.flex = '1';
      body.style.minHeight = '0';
    }
    body.style.overflow = 'auto';

    const pre = document.createElement('pre');
    body.appendChild(pre);

    this.container.appendChild(tb);
    this.container.appendChild(levelBar);
    this.container.appendChild(body);
    this._body = body;
    this._pre = pre;
    this._followBtn = followBtn;
    this._levelBar = levelBar;

    body.addEventListener('scroll', () => {
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 30;
      if (atBottom) { this.follow = true; this._followBtn.className = 'active'; }
      else if (this.follow) { this.follow = false; this._followBtn.className = ''; }
    });
  }

  connect() {
    if (this.ws) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/logs/${this.vmId}/stream?format=json&log=${this.logName}`;
    this.ws = new WebSocket(url);
    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'chunk') this._appendText(msg.data);
        else if (msg.type === 'reset') { this.lines = []; this._appendText('--- ' + msg.data + ' ---\n'); }
      } catch { this._appendText(e.data); }
    };
    this.ws.onclose = () => { this.ws = null; };
    this.ws.onerror = () => { this.ws?.close(); };
  }

  disconnect() { if (this.ws) { this.ws.close(); this.ws = null; } }

  _appendText(text) {
    const newLines = text.split('\n');
    if (this.lines.length && newLines.length) this.lines[this.lines.length - 1] += newLines.shift();
    if (text.endsWith('\n')) this.lines.push(...newLines.slice(0, -1));
    else this.lines.push(...newLines);
    if (this.lines.length > 20000) this.lines = this.lines.slice(-15000);
    this._renderLines();
    this._updateLevelBar();
    if (this.follow) this._scrollToBottom();
  }

  _renderLines() {
    let lines = this.lines;
    if (this.levelFilter) lines = lines.filter(l => classifyLevel(l) === 'lvl-' + this.levelFilter);
    if (this.searchTerm) {
      const q = this.searchTerm.toLowerCase();
      lines = lines.filter(l => l.toLowerCase().includes(q));
    }
    const html = lines.map(line => {
      const cls = classifyLevel(line);
      let escaped = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      if (this.searchTerm) {
        const re = new RegExp(this.searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
        escaped = escaped.replace(re, m => `<mark class="log-match">${m}</mark>`);
      }
      return cls ? `<span class="${cls}">${escaped}</span>` : escaped;
    }).join('\n');
    this._pre.innerHTML = html;
  }

  _updateLevelBar() {
    let e = 0, w = 0, i = 0, d = 0;
    for (const l of this.lines) {
      const c = classifyLevel(l);
      if (c === 'lvl-error') e++;
      else if (c === 'lvl-warn') w++;
      else if (c === 'lvl-debug') d++;
      else i++;
    }
    const spans = this._levelBar.querySelectorAll('span');
    spans[0].textContent = e; spans[1].textContent = w;
    spans[2].textContent = i; spans[3].textContent = d;
  }

  _scrollToBottom() { this._body.scrollTop = this._body.scrollHeight; }
  clear() { this.lines = []; this._renderLines(); }
}

// ---- DebugStatsPanel ----

class DebugStatsPanel {
  constructor(container, vmId) {
    this.container = container;
    this.vmId = vmId;
    this._timer = null;
    this._build();
  }

  _build() {
    this.container.innerHTML = `
      <table class="stats-table">
        <tr><td>FPS</td><td id="ds-fps">--</td></tr>
        <tr><td>Latency</td><td id="ds-lat">--</td></tr>
        <tr><td>Frames</td><td id="ds-frames">--</td></tr>
        <tr><td>Memory</td><td id="ds-mem">--</td></tr>
        <tr><td>Process</td><td id="ds-proc">--</td></tr>
        <tr><td>Video</td><td id="ds-vid">--</td></tr>
        <tr><td>Input</td><td id="ds-inp">--</td></tr>
      </table>`;
  }

  start(intervalMs = 3000) { this._poll(); this._timer = setInterval(() => this._poll(), intervalMs); }
  stop() { if (this._timer) { clearInterval(this._timer); this._timer = null; } }

  async _poll() {
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/metrics`);
      if (!resp.ok) return;
      const m = await resp.json();
      const fps = m.fps?.current || 0;
      const fpsEl = document.getElementById('ds-fps');
      if (fpsEl) { fpsEl.textContent = fps.toFixed(1) + ' fps'; fpsEl.className = fps >= 18 ? 'lvl-info' : fps >= 10 ? 'lvl-warn' : 'lvl-error'; }
      const latEl = document.getElementById('ds-lat');
      if (latEl) latEl.textContent = `avg ${(m.latency?.avg_ms || 0).toFixed(0)}ms / p95 ${(m.latency?.p95_ms || 0).toFixed(0)}ms`;
      const framesEl = document.getElementById('ds-frames');
      if (framesEl) framesEl.textContent = `${m.video?.frames_received || 0} recv, ${m.video?.frames_dropped || 0} drop`;
      const memEl = document.getElementById('ds-mem');
      if (memEl) memEl.textContent = `${(m.process?.rss_mb || 0).toFixed(0)} MB RSS`;
      const procEl = document.getElementById('ds-proc');
      if (procEl) {
        const pid = m.process?.pid || '?';
        const up = m.process?.uptime_sec || 0;
        procEl.textContent = `PID ${pid}, up ${Math.floor(up / 60)}m ${Math.floor(up % 60)}s`;
      }
      const vidEl = document.getElementById('ds-vid');
      if (vidEl) {
        const up = m.video?.uptime_sec || 0;
        vidEl.textContent = m.video?.connected ? `connected (${Math.floor(up / 60)}m)` : 'disconnected';
        vidEl.className = m.video?.connected ? 'lvl-info' : 'lvl-error';
      }
      const inpEl = document.getElementById('ds-inp');
      if (inpEl) {
        inpEl.textContent = m.input?.connected ? `connected (${m.input?.commands_ok || 0} cmds)` : 'disconnected';
        inpEl.className = m.input?.connected ? 'lvl-info' : 'lvl-error';
      }
    } catch {}
  }
}

// ---- DebugCommandInspector ----

class DebugCommandInspector {
  constructor(container, opts = {}) {
    this.container = container;
    this.maxEntries = opts.maxEntries || 50;
    this.entries = [];
    this._build();
  }

  _build() {
    this.container.innerHTML = '';
    this.container.style.cssText = 'max-height:200px;overflow:auto;font-size:10px;font-family:var(--font-mono,monospace)';
  }

  recordCommand(type, summary, ok, latencyMs) {
    this.entries.push({
      time: new Date().toISOString().substr(11, 12),
      type, summary, ok,
      latencyMs: Math.round(latencyMs * 10) / 10,
    });
    if (this.entries.length > this.maxEntries) this.entries = this.entries.slice(-this.maxEntries);
    this._render();
    this.container.scrollTop = this.container.scrollHeight;
  }

  _render() {
    this.container.innerHTML = this.entries.map(e => {
      const cls = e.ok ? 'cmd-ok' : 'cmd-err';
      return `<div class="cmd-row">
        <span class="cmd-time">${e.time}</span>
        <span class="cmd-type">${e.type}</span>
        <span class="cmd-summary">${_escHtml(e.summary)}</span>
        <span class="cmd-latency ${cls}">${e.latencyMs}ms</span>
      </div>`;
    }).join('');
  }

  clear() { this.entries = []; this._render(); }
}

// ---- DebugAppManager ----

class DebugAppManager {
  constructor(container, vmId) {
    this.container = container;
    this.vmId = vmId;
    this.apps = [];
    this.filter = '';
    this._build();
    this.refresh();
  }

  _build() {
    this.container.innerHTML = `
      <div class="dbg-toolbar">
        <input type="text" id="appFilter" placeholder="Filter apps…" style="flex:1" />
        <button id="appRefresh">⟳ Refresh</button>
      </div>
      <div id="appList" class="app-list"></div>
      <div style="border-top:1px solid var(--border);padding-top:8px;margin-top:8px">
        <div class="muted" style="font-size:11px; margin-bottom:6px">Launch by Bundle ID</div>
        <div class="row">
          <input type="text" id="manualBundleId" placeholder="com.example.app" style="flex:1;font-size:11px" />
          <button id="manualLaunch">▶ Launch</button>
        </div>
      </div>
      <div id="appStatus" class="dbg-status muted"></div>`;
    this.container.querySelector('#appRefresh').onclick = () => this.refresh();
    this.container.querySelector('#appFilter').addEventListener('input', (e) => {
      this.filter = e.target.value.toLowerCase();
      this._render();
    });
    this.container.querySelector('#manualLaunch').onclick = () => {
      const bid = this.container.querySelector('#manualBundleId').value.trim();
      if (bid) this._launch(bid);
    };
    this.container.querySelector('#manualBundleId').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        const bid = e.target.value.trim();
        if (bid) this._launch(bid);
      }
    });
  }

  async refresh() {
    const status = this.container.querySelector('#appStatus');
    status.textContent = 'Loading apps…';
    status.className = 'dbg-status muted';
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps`, { credentials: 'same-origin' });
      if (resp.status === 404) {
        this.apps = [];
        status.textContent = '⚠ Route not found (404). Restart the backend server to load new routes.';
        status.className = 'dbg-status err';
        this._render();
        return;
      }
      if (resp.status === 409) {
        this.apps = [];
        status.textContent = '⚠ VM is not ready. Start the VM first.';
        status.className = 'dbg-status err';
        this._render();
        return;
      }
      const data = await resp.json();
      if (data.ok && data.apps) {
        this.apps = Array.isArray(data.apps) ? data.apps : [];
        status.textContent = `${this.apps.length} apps found`;
        status.className = 'dbg-status ok';
      } else if (data.error === 'guest not connected') {
        this.apps = [];
        status.textContent = '⏳ Guest agent not connected yet. VM is still booting — wait ~30s and refresh.';
        status.className = 'dbg-status muted';
      } else if (data.ok === false && data.error) {
        this.apps = [];
        status.textContent = '⚠ ' + data.error + ' — Use "Launch by Bundle ID" below.';
        status.className = 'dbg-status err';
      } else {
        this.apps = [];
        status.textContent = 'app_list not supported. Use "Launch by Bundle ID" below.';
        status.className = 'dbg-status muted';
      }
    } catch (e) {
      this.apps = [];
      status.textContent = 'Error: ' + e.message;
      status.className = 'dbg-status err';
    }
    this._render();
  }

  _render() {
    const el = this.container.querySelector('#appList');
    let apps = this.apps;
    if (this.filter) {
      apps = apps.filter(a => {
        const name = (a.name || a.bundle_id || '').toLowerCase();
        const bid = (a.bundle_id || '').toLowerCase();
        return name.includes(this.filter) || bid.includes(this.filter);
      });
    }
    if (!apps.length) {
      el.innerHTML = '<div class="muted" style="padding:12px;text-align:center;font-size:11px">No apps match. Try refreshing or use the manual launch input.</div>';
      return;
    }
    el.innerHTML = apps.map(a => {
      const name = _escHtml(a.name || a.bundle_id || '?');
      const bid = _escHtml(a.bundle_id || '');
      const type = a.type === 'system' ? '<span class="app-badge sys">SYS</span>' : '<span class="app-badge usr">USER</span>';
      const pid = a.pid || 0;
      const state = a.state || '';
      const isRunning = pid > 0;
      const runBadge = isRunning ? `<span class="app-badge run" title="PID=${pid}">● RUN</span>` : '';
      const pidInfo = isRunning ? `<span class="muted" style="font-size:9px">PID ${pid}</span>` : '';
      return `<div class="app-row" data-bid="${bid}">
        <div class="app-info" style="cursor:pointer">
          <div class="app-name">${name} ${type} ${runBadge}</div>
          <div class="app-bid mono">${bid}</div>
          <div style="display:flex;gap:6px">
            ${a.version ? `<span class="muted" style="font-size:9px">v${_escHtml(a.version)}</span>` : ''}
            ${pidInfo}
          </div>
        </div>
        <div class="app-actions">
          <button class="app-debug" data-bid="${bid}" title="Debug">🔍</button>
          ${isRunning ? `<button class="app-kill-btn" data-bid="${bid}" title="Kill PID ${pid}">✕</button>` : ''}
          <button class="app-launch" data-bid="${bid}" title="Launch">▶</button>
        </div>
      </div>`;
    }).join('');
    el.querySelectorAll('.app-launch').forEach(btn => {
      btn.onclick = () => this._launch(btn.dataset.bid);
    });
    el.querySelectorAll('.app-kill-btn').forEach(btn => {
      btn.onclick = () => { this._kill(btn.dataset.bid); setTimeout(() => this.refresh(), 500); };
    });
    el.querySelectorAll('.app-debug').forEach(btn => {
      btn.onclick = () => this._debugApp(btn.dataset.bid);
    });
    // Click on app info row → open debug panel
    el.querySelectorAll('.app-info').forEach(info => {
      info.onclick = () => {
        const bid = info.parentElement.dataset.bid;
        if (bid) this._debugApp(bid);
      };
    });
  }

  _debugApp(bundleId) {
    const app = this.apps.find(a => a.bundle_id === bundleId) || { bundle_id: bundleId, name: bundleId };
    const name = app.name || bundleId;
    const version = app.version || '?';
    const appType = app.type || 'unknown';
    const appPath = app.path || '?';
    const dataPath = app.data || '?';
    const shortBid = bundleId.length > 50 ? bundleId.slice(0, 47) + '…' : bundleId;

    const status = this.container.querySelector('#appStatus');
    const list = this.container.querySelector('#appList');

    list.innerHTML = `
      <div class="app-debug-panel">
        <div class="dbg-toolbar">
          <button id="appDebugBack">← Back</button>
          <span class="mono" style="font-size:12px;font-weight:600">${_escHtml(name)}</span>
          <span class="muted mono" style="font-size:10px">${_escHtml(shortBid)}</span>
          <span style="flex:1"></span>
          <button id="appDebugLaunch">▶ Launch</button>
          <button id="appDebugKill" class="danger">✕ Kill</button>
          <button id="appDebugAutoDebug" style="background:var(--green-dim);color:var(--green);border-color:var(--green)">🚀 Auto-Debug</button>
        </div>

        <div class="debug-tabs" style="margin-bottom:0">
          <button class="debug-tab active" data-dt="info">Info</button>
          <button class="debug-tab" data-dt="applog">App Log</button>
          <button class="debug-tab" data-dt="kernellog">Kernel</button>
          <button class="debug-tab" data-dt="syslog">System</button>
          <button class="debug-tab" data-dt="crashlog">Crashes</button>
          <button class="debug-tab" data-dt="lldb">LLDB</button>
        </div>

        <div id="appDebugTabInfo" class="app-debug-tab-content">
          <div class="sys-section">
            <table class="stats-table">
              <tr><td>Bundle ID</td><td class="mono" style="font-size:10px;word-break:break-all">${_escHtml(bundleId)}</td></tr>
              <tr><td>Name</td><td>${_escHtml(name)}</td></tr>
              <tr><td>Version</td><td>${_escHtml(version)}</td></tr>
              <tr><td>Type</td><td>${appType}</td></tr>
              <tr id="appDebugInfoPid"><td>PID</td><td class="mono"><span class="muted">Loading…</span></td></tr>
              <tr id="appDebugInfoState"><td>State</td><td><span class="muted">Loading…</span></td></tr>
              <tr><td>Path</td><td class="mono" style="font-size:9px;word-break:break-all">${_escHtml(appPath)}</td></tr>
              <tr><td>Data</td><td class="mono" style="font-size:9px;word-break:break-all">${_escHtml(dataPath)}</td></tr>
            </table>
          </div>
          <div class="sys-section">
            <div class="row" style="gap:6px">
              <button id="appDebugLaunch2">▶ Launch</button>
              <button id="appDebugKill2" class="danger">✕ Kill</button>
              <button id="appDebugForeground">◎ Foreground</button>
            </div>
            <div id="appDebugActionResult" class="muted" style="font-size:11px;margin-top:8px"></div>
          </div>
        </div>
        <div id="appDebugTabApplog" class="app-debug-tab-content" style="display:none;flex:1;min-height:0">
          <div class="log-viewer" style="flex:1;min-height:0">
            <div class="log-toolbar-mini">
              <span class="muted" style="font-size:10px">boot.log lines matching "${_escHtml(shortBid)}"</span>
              <span style="flex:1"></span>
              <button id="appLogRefresh">⟳ Refresh</button>
            </div>
            <div id="appDebugLogApp" class="log-body-mini" style="flex:1;min-height:0;max-height:350px">Loading…</div>
          </div>
        </div>
        <div id="appDebugTabKernellog" class="app-debug-tab-content" style="display:none;flex:1;min-height:0">
          <div class="log-viewer" style="flex:1;min-height:0">
            <div class="log-toolbar-mini">
              <span class="muted" style="font-size:10px">Serial console + kernel messages (last 200 lines)</span>
              <span style="flex:1"></span>
              <button id="appKernelRefresh">⟳ Refresh</button>
            </div>
            <div id="appDebugLogKernel" class="log-body-mini" style="flex:1;min-height:0;max-height:350px">Loading…</div>
          </div>
        </div>
        <div id="appDebugTabSyslog" class="app-debug-tab-content" style="display:none;flex:1;min-height:0">
          <div class="log-viewer" style="flex:1;min-height:0">
            <div class="log-toolbar-mini">
              <span class="muted" style="font-size:10px">vphoned + system messages (last 200 lines)</span>
              <span style="flex:1"></span>
              <button id="appSysRefresh">⟳ Refresh</button>
            </div>
            <div id="appDebugLogSys" class="log-body-mini" style="flex:1;min-height:0;max-height:350px">Loading…</div>
          </div>
        </div>
        <div id="appDebugTabCrashlog" class="app-debug-tab-content" style="display:none;flex:1;min-height:0">
          <div class="log-viewer" style="flex:1;min-height:0">
            <div class="log-toolbar-mini">
              <span class="muted" style="font-size:10px">Crash reports for this app</span>
              <span style="flex:1"></span>
              <button id="appCrashRefresh">⟳ Refresh</button>
            </div>
            <div id="appDebugLogCrash" class="log-body-mini" style="flex:1;min-height:0;max-height:350px">Loading…</div>
          </div>
        </div>
        <div id="appDebugTabLldb" class="app-debug-tab-content" style="display:none;flex:1;min-height:0">
          <div class="sys-section">
            <div class="sys-title" style="display:flex;align-items:center;gap:8px">
              <span>🛠 LLDB Terminal</span>
              <span id="lldbStatus" class="muted" style="font-size:10px">not connected</span>
              <span style="flex:1"></span>
              <button id="lldbConnectBtn" style="font-size:10px;padding:3px 8px">🔌 Connect</button>
              <button id="lldbDisconnectBtn" class="danger" style="font-size:10px;padding:3px 8px;display:none">⏹ Stop</button>
            </div>
          </div>
          <div id="lldbTerminal" style="flex:1;min-height:0;display:flex;flex-direction:column;background:#0d0d0d;border:1px solid var(--border);overflow:hidden">
            <div id="lldbOutput" style="flex:1;overflow:auto;padding:8px;font-family:var(--font-mono);font-size:11px;line-height:1.4;white-space:pre-wrap;color:var(--fg)"></div>
            <div style="display:flex;border-top:1px solid var(--border);align-items:center;background:var(--bg)">
              <span style="color:var(--green);padding:0 8px;font-family:var(--font-mono);font-size:11px">(lldb)</span>
              <input id="lldbInput" type="text" placeholder="Type LLDB command…" style="flex:1;border:none;background:transparent;color:var(--fg);font-family:var(--font-mono);font-size:11px;padding:6px 8px;outline:none" />
              <button id="lldbSend" style="font-size:10px;padding:4px 8px;margin:2px">Send</button>
            </div>
          </div>
          <details>
            <summary class="muted" style="font-size:10px;cursor:pointer;padding:4px 0">LLDB Quick Reference</summary>
            <div class="muted mono" style="font-size:9px;line-height:1.5;background:var(--bg);padding:6px;max-height:150px;overflow:auto;display:grid;grid-template-columns:1fr 1fr;gap:2px 12px">
              <div>b &lt;func&gt; — breakpoint</div><div>c — continue</div>
              <div>n — step over</div><div>s — step into</div>
              <div>finish — step out</div><div>bt — backtrace</div>
              <div>p &lt;expr&gt; — print</div><div>po &lt;expr&gt; — print obj</div>
              <div>frame variable — locals</div><div>reg read — registers</div>
              <div>dis -f — disassemble</div><div>image list — modules</div>
              <div>br list — breakpoints</div><div>watchpoint &lt;v&gt; — watch</div>
            </div>
          </details>
        </div>
      </div>`;

    // Tab switching
    list.querySelectorAll('.debug-tab[data-dt]').forEach(tab => {
      tab.onclick = () => {
        list.querySelectorAll('.debug-tab[data-dt]').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        list.querySelectorAll('.app-debug-tab-content').forEach(c => c.style.display = 'none');
        const target = document.getElementById('appDebugTab' + tab.dataset.dt.charAt(0).toUpperCase() + tab.dataset.dt.slice(1));
        if (target) target.style.display = '';
      };
    });

    // Wire buttons
    document.getElementById('appDebugBack').onclick = () => this.refresh();
    document.getElementById('appDebugLaunch').onclick = () => this._launch(bundleId);
    document.getElementById('appDebugLaunch2').onclick = () => this._launch(bundleId);
    document.getElementById('appDebugKill').onclick = () => this._kill(bundleId);
    document.getElementById('appDebugKill2').onclick = () => this._kill(bundleId);
    document.getElementById('appDebugForeground').onclick = () => this._foreground(bundleId);
    document.getElementById('appDebugAutoDebug').onclick = () => this._autoDebug(bundleId);

    // Init LLDB terminal
    let lldbWs = null;
    const lldbOutput = document.getElementById('lldbOutput');
    const lldbInput = document.getElementById('lldbInput');
    const lldbStatus = document.getElementById('lldbStatus');

    function lldbTermAppend(text) {
      if (!lldbOutput) return;
      lldbOutput.textContent += text;
      lldbOutput.scrollTop = lldbOutput.scrollHeight;
    }

    function connectLldbTerminal() {
      if (lldbWs) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      lldbWs = new WebSocket(`${proto}//${location.host}/api/vms/${this.vmId}/lldb-terminal`);
      lldbWs.onopen = () => {
        if (lldbStatus) { lldbStatus.textContent = '● connected'; lldbStatus.style.color = 'var(--green)'; }
        lldbTermAppend('=== LLDB Terminal Connected ===\n');
      };
      lldbWs.onmessage = (e) => lldbTermAppend(e.data);
      lldbWs.onclose = () => {
        if (lldbStatus) { lldbStatus.textContent = '○ disconnected'; lldbStatus.style.color = 'var(--muted)'; }
        lldbWs = null;
      };
    }

    function disconnectLldbTerminal() {
      if (lldbWs) { lldbWs.close(); lldbWs = null; }
    }

    function sendLldbCommand() {
      const cmd = lldbInput.value.trim();
      if (!cmd || !lldbWs) return;
      lldbWs.send(cmd);
      lldbTermAppend(`(lldb) ${cmd}\n`);
      lldbInput.value = '';
    }

    document.getElementById('lldbSend').onclick = () => sendLldbCommand();
    lldbInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') sendLldbCommand();
    });
    document.getElementById('lldbConnectBtn').onclick = () => { connectLldbTerminal(); this._autoDebug(bundleId); };
    document.getElementById('lldbDisconnectBtn').onclick = () => {
      disconnectLldbTerminal();
      document.getElementById('lldbConnectBtn').style.display = '';
      document.getElementById('lldbDisconnectBtn').style.display = 'none';
    };

    // Load all log categories + LLDB info
    this._loadDebugLogs(bundleId);
    this._loadLldbInfo(bundleId);
    document.getElementById('appLogRefresh').onclick = () => this._loadDebugLogs(bundleId);
    document.getElementById('appKernelRefresh').onclick = () => this._loadDebugLogs(bundleId);
    document.getElementById('appSysRefresh').onclick = () => this._loadDebugLogs(bundleId);
    document.getElementById('appCrashRefresh').onclick = () => this._loadDebugLogs(bundleId);

    status.textContent = `Debugging: ${name}`;
    status.className = 'dbg-status ok';
  }

  async _loadDebugLogs(bundleId) {
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps/${encodeURIComponent(bundleId)}/debug-logs?lines=200`, {
        credentials: 'same-origin',
      });
      if (!resp.ok) return;
      const data = await resp.json();
      this._fillLog('appDebugLogApp', data.app_logs || [], 'No app-specific logs found. Launch the app first.');
      this._fillLog('appDebugLogKernel', data.kernel_logs || [], 'No kernel logs available.');
      this._fillLog('appDebugLogSys', data.system_logs || [], 'No system logs available.');
      this._fillLog('appDebugLogCrash', data.crash_logs || [], 'No crash reports for this app.');
    } catch (e) {
      // ignore
    }
  }

  async _loadLldbInfo(bundleId) {
    // Get current PID for this app
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps`, { credentials: 'same-origin' });
      const data = await resp.json();
      const apps = data.apps || [];
      const app = apps.find(a => a.bundle_id === bundleId);
      const pid = app ? (app.pid || 0) : 0;
      const appName = app ? (app.name || bundleId) : bundleId;

      // Get VM IP from diagnostics
      let vmIP = '192.168.64.11'; // default virtio network
      try {
        const diagResp = await fetch(`/api/vms/${this.vmId}/diagnostics`, { credentials: 'same-origin' });
        const diag = await diagResp.json();
        // Extract IP from boot.log tail
        const bootLines = diag.logs_tail?.boot || [];
        for (const line of bootLines) {
          const m = line.match(/connected to vphoned[^(]*\(([0-9.]+)\)/);
          if (m) { vmIP = m[1]; break; }
        }
      } catch {}

      // Update Info tab PID/State
      const pidRow = document.getElementById('appDebugInfoPid');
      const stateRow = document.getElementById('appDebugInfoState');
      if (pidRow && app) {
        pidRow.innerHTML = '<td>PID</td><td class="mono">' + (pid > 0
          ? `<span style="color:var(--green)">${pid}</span>`
          : '<span class="muted">not running</span>') + '</td>';
      }
      if (stateRow && app) {
        const st = app.state || '?';
        stateRow.innerHTML = '<td>State</td><td>' + (st === 'running'
          ? '<span style="color:var(--green)">● running</span>'
          : '<span class="muted">' + _escHtml(st) + '</span>') + '</td>';
      }

      // Update debugserver run command
      const runEl = document.getElementById('lldbRunCmd');
      if (runEl) {
        if (pid > 0) {
          runEl.innerHTML = `
            <div style="color:var(--green)">debugserver localhost:6666 --attach=${pid}</div>
            <div style="margin-top:2px;color:var(--muted)"># App: ${_escHtml(appName)} (PID ${pid})</div>
            <div style="color:var(--muted)"># Hoặc: debugserver localhost:6666 -a "${_escHtml(appName)}"</div>`;
        } else {
          runEl.innerHTML = `
            <div style="color:var(--amber)">App not running. Launch the app first.</div>
            <div style="margin-top:2px;color:var(--muted)"># Sau khi launch:</div>
            <div style="color:var(--green)">debugserver localhost:6666 -a "${_escHtml(appName)}"</div>`;
        }
      }

      // Update LLDB connect command
      const connEl = document.getElementById('lldbConnectCmd');
      if (connEl) {
        connEl.innerHTML = `
          <div style="color:var(--muted)"># Trên Mac (Terminal):</div>
          <div style="color:var(--green)">lldb</div>
          <div style="color:var(--green)">(lldb) process connect connect://${vmIP}:6666</div>
          <div style="margin-top:4px;color:var(--muted)"># Hoặc 1 dòng:</div>
          <div style="color:var(--green)">lldb -o "process connect connect://${vmIP}:6666"</div>
          ${pid > 0 ? `<div style="margin-top:4px;color:var(--muted)"># Attach xong có thể dùng các lệnh LLDB bên dưới để debug app "${_escHtml(appName)}" (PID ${pid})</div>` : ''}`;
      }
    } catch {}
  }

  _fillLog(elId, lines, emptyMsg) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!lines.length) {
      el.innerHTML = `<span class="muted">${emptyMsg}</span>`;
      return;
    }
    el.innerHTML = lines.map(l => {
      let escaped = l.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      const cls = classifyLevel(l);
      return cls ? `<span class="${cls}">${escaped}</span>` : escaped;
    }).join('\n');
  }

  async _kill(bundleId) {
    const status = this.container.querySelector('#appStatus');
    const resultEl = document.getElementById('appDebugActionResult');
    status.textContent = `Killing ${bundleId}…`;
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps/${encodeURIComponent(bundleId)}/kill`, {
        method: 'POST', credentials: 'same-origin',
      });
      const data = await resp.json();
      if (resultEl) {
        resultEl.textContent = data.ok ? `✓ Killed ${bundleId}` : `✗ ${data.error || 'Kill failed'}`;
        resultEl.className = data.ok ? 'ok' : 'err';
      }
      status.textContent = data.ok ? `✓ Killed ${bundleId}` : `✗ ${data.error || 'Kill failed'}`;
    } catch (e) {
      if (resultEl) { resultEl.textContent = '✗ ' + e.message; resultEl.className = 'err'; }
      status.textContent = '✗ ' + e.message;
    }
  }

  async _autoDebug(bundleId) {
    const status = this.container.querySelector('#appStatus');
    const resultEl = document.getElementById('appDebugActionResult');
    status.textContent = '🚀 Auto-Debug: launching app + starting debugserver + preparing LLDB…';
    if (resultEl) { resultEl.textContent = '⏳ Working…'; resultEl.className = ''; }

    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps/${encodeURIComponent(bundleId)}/debug`, {
        method: 'POST', credentials: 'same-origin',
      });
      const data = await resp.json();
      if (data.ok) {
        const lldbCmd = data.lldb_command || 'lldb -o "process connect connect://VM_IP:6666"';
        const dsUp = data.debugserver_uploaded;
        const dsStart = data.debugserver_started;
        if (resultEl) {
          resultEl.innerHTML = `
            <div style="color:var(--green)">✓ Debugger ready! PID=${data.pid}</div>
            <div style="margin-top:6px;font-size:10px;color:var(--muted)">
              <div>📤 Upload debugserver: ${dsUp ? '✅ auto-uploaded' : '⚠️ not found on Mac (install Xcode)'}</div>
              <div>🚀 Start debugserver: ${dsStart ? '✅ running (PID ' + data.debugserver_pid + ')' : '⚠️ could not start'}</div>
              <div style="margin-top:6px">Run on your Mac:</div>
              <div class="mono" style="color:var(--green);margin-top:2px;background:var(--bg);padding:6px;cursor:pointer;border:1px solid var(--green)" onclick="navigator.clipboard.writeText('${lldbCmd.replace(/'/g, "\\'").replace(/"/g, '&quot;')}')" title="Click to copy">${lldbCmd}</div>
              <div style="color:var(--blue);font-size:9px;margin-top:2px">⬆ Click to copy → paste in Terminal</div>
              ${dsStart ? '<div style="color:var(--green);margin-top:4px">⚡ debugserver is running on VM — LLDB will connect instantly!</div>' : ''}
            </div>`;
        }
        status.textContent = dsStart
          ? `⚡ Debugger LIVE for ${data.app_name} (PID ${data.pid}) — use LLDB Terminal tab`
          : `🚀 ${data.app_name} (PID ${data.pid}) — upload debugserver, then run LLDB command`;
        status.className = 'dbg-status ok';

        // Auto-connect WebSocket terminal if LLDB started
        if (data.lldb_started && !lldbWs) {
          connectLldbTerminal();
          document.getElementById('lldbConnectBtn').style.display = 'none';
          document.getElementById('lldbDisconnectBtn').style.display = '';
          if (lldbStatus) { lldbStatus.textContent = '● auto-connecting…'; lldbStatus.style.color = 'var(--amber)'; }
        }
      } else {
        if (resultEl) { resultEl.textContent = '✗ ' + (data.error || 'Auto-debug failed'); resultEl.className = 'err'; }
        status.textContent = '✗ ' + (data.error || 'Auto-debug failed');
        status.className = 'dbg-status err';
      }
    } catch (e) {
      if (resultEl) { resultEl.textContent = '✗ ' + e.message; resultEl.className = 'err'; }
      status.textContent = '✗ ' + e.message;
      status.className = 'dbg-status err';
    }
  }

  async _foreground(bundleId) {
    const status = this.container.querySelector('#appStatus');
    const resultEl = document.getElementById('appDebugActionResult');
    status.textContent = `Bringing ${bundleId} to foreground…`;
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps/${encodeURIComponent(bundleId)}/foreground`, {
        method: 'POST', credentials: 'same-origin',
      });
      const data = await resp.json();
      if (resultEl) {
        resultEl.textContent = data.ok ? `✓ ${bundleId} → foreground` : `✗ ${data.error || 'Failed'}`;
        resultEl.className = data.ok ? 'ok' : 'err';
      }
      status.textContent = data.ok ? `✓ ${bundleId} in foreground` : `✗ ${data.error || 'Failed'}`;
    } catch (e) {
      if (resultEl) { resultEl.textContent = '✗ ' + e.message; resultEl.className = 'err'; }
      status.textContent = '✗ ' + e.message;
    }
  }

  async _launch(bundleId) {
    const status = this.container.querySelector('#appStatus');
    status.textContent = `Launching ${bundleId}…`;
    status.className = 'dbg-status';
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/apps/${encodeURIComponent(bundleId)}/launch`, {
        method: 'POST', credentials: 'same-origin',
      });
      if (resp.status === 404) {
        status.textContent = '⚠ Route not found. Restart backend server.';
        status.className = 'dbg-status err';
        return;
      }
      const data = await resp.json();
      status.textContent = data.ok ? `✓ Launched ${bundleId}` : `✗ ${data.error || 'Launch failed'}`;
      status.className = data.ok ? 'dbg-status ok' : 'dbg-status err';
    } catch (e) {
      status.textContent = '✗ ' + e.message;
      status.className = 'dbg-status err';
    }
  }
}

// ---- DebugCrashViewer ----

class DebugCrashViewer {
  constructor(container, vmId) {
    this.container = container;
    this.vmId = vmId;
    this.crashes = [];
    this._build();
    this.refresh();
    this._interval = setInterval(() => this.refresh(), 30000);
  }

  _build() {
    this.container.innerHTML = `
      <div class="dbg-toolbar">
        <span class="muted" style="font-size:11px">Apple Crash Reports</span>
        <span style="flex:1"></span>
        <button id="crashRefresh">⟳ Refresh</button>
      </div>
      <div id="crashList" class="crash-list"></div>
      <div id="crashDetail" class="crash-detail" style="display:none">
        <div class="dbg-toolbar">
          <button id="crashBack">← Back</button>
          <span id="crashFileName" class="mono" style="font-size:11px;flex:1;margin-left:8px"></span>
        </div>
        <pre id="crashContent" class="crash-content"></pre>
      </div>
      <div id="crashStatus" class="dbg-status muted"></div>`;
    this.container.querySelector('#crashRefresh').onclick = () => this.refresh();
    this.container.querySelector('#crashBack').onclick = () => this._showList();
  }

  async refresh() {
    const status = this.container.querySelector('#crashStatus');
    status.textContent = 'Scanning crash reports…';
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/crashes`, { credentials: 'same-origin' });
      const data = await resp.json();
      this.crashes = data.crashes || [];
      status.textContent = this.crashes.length
        ? `${this.crashes.length} crash reports found`
        : 'No crash reports found';
    } catch (e) {
      status.textContent = 'Error: ' + e.message;
    }
    this._renderList();
  }

  _renderList() {
    const el = this.container.querySelector('#crashList');
    if (!this.crashes.length) {
      el.innerHTML = '<div class="muted" style="padding:16px;text-align:center">No crash reports on device.<br><span style="font-size:10px">Reports appear after an app crash.</span></div>';
      return;
    }
    el.innerHTML = this.crashes.map(c => {
      const name = _escHtml(c.name);
      const isIPS = name.endsWith('.ips');
      const icon = isIPS ? '💥' : '📋';
      return `<div class="crash-row" data-path="${_escHtml(c.dir + '/' + c.name)}">
        <span>${icon}</span>
        <span class="crash-name mono">${name}</span>
      </div>`;
    }).join('');
    el.querySelectorAll('.crash-row').forEach(row => {
      row.onclick = () => this._openCrash(row.dataset.path);
    });
  }

  async _openCrash(path) {
    const detail = this.container.querySelector('#crashDetail');
    const list = this.container.querySelector('#crashList');
    const content = this.container.querySelector('#crashContent');
    const fname = this.container.querySelector('#crashFileName');

    list.style.display = 'none';
    detail.style.display = '';
    fname.textContent = path.split('/').pop();
    content.textContent = 'Loading…';

    try {
      const resp = await fetch(`/api/vms/${this.vmId}/crashes/${encodeURIComponent(path)}`, { credentials: 'same-origin' });
      const data = await resp.json();
      if (data.ok && data.data) {
        content.innerHTML = this._highlightCrash(data.data);
      } else if (data.content) {
        content.innerHTML = this._highlightCrash(data.content);
      } else {
        content.textContent = data.error || 'Failed to load crash report';
      }
    } catch (e) {
      content.textContent = 'Error: ' + e.message;
    }
  }

  _highlightCrash(text) {
    const lines = text.split('\n');
    return lines.map(line => {
      let escaped = _escHtml(line);
      // Highlight crash-specific patterns
      if (/^(Exception Type|Exception Subtype|Exception Codes|Signal|Termination)/i.test(line)) {
        return `<span class="crash-hl-exc">${escaped}</span>`;
      }
      if (/^(Thread \d+|Crashed|Binary Images)/i.test(line)) {
        return `<span class="crash-hl-thread">${escaped}</span>`;
      }
      if (/^\d+\s+\S+\s+0x[0-9a-f]+/.test(line)) {
        return `<span class="crash-hl-frame">${escaped}</span>`;
      }
      if (/^(Process|Path|Identifier|Version|Code Type|Parent Process|Date)/i.test(line)) {
        return `<span class="crash-hl-meta">${escaped}</span>`;
      }
      return escaped;
    }).join('\n');
  }

  _showList() {
    this.container.querySelector('#crashDetail').style.display = 'none';
    this.container.querySelector('#crashList').style.display = '';
  }

  destroy() { if (this._interval) clearInterval(this._interval); }
}

// ---- DebugNetworkInspector ----

class DebugNetworkInspector {
  constructor(container) {
    this.container = container;
    this.requests = [];
    this.maxEntries = 200;
    this.filter = '';
    this.selectedIdx = -1;
    this._build();
    this._intercept();
  }

  _build() {
    this.container.innerHTML = `
      <div class="dbg-toolbar">
        <input type="text" id="netFilter" placeholder="Filter URL…" style="flex:1" />
        <button id="netClear">Clear</button>
      </div>
      <div class="net-split">
        <div id="netList" class="net-list"></div>
        <div id="netDetail" class="net-detail">
          <div class="muted" style="padding:12px;text-align:center;font-size:11px">Select a request to view details</div>
        </div>
      </div>
      <div class="net-summary" id="netSummary">0 requests</div>`;
    this.container.querySelector('#netClear').onclick = () => { this.requests = []; this.selectedIdx = -1; this._render(); };
    this.container.querySelector('#netFilter').addEventListener('input', (e) => { this.filter = e.target.value.toLowerCase(); this._render(); });
  }

  _intercept() {
    const self = this;
    const origFetch = window.fetch.bind(window);
    window.fetch = function (...args) {
      // Record the request (non-blocking, never breaks actual fetch)
      let entry;
      try {
        const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '?';
        const opts = args[1] || {};
        const method = (opts.method || 'GET').toUpperCase();
        entry = {
          id: self.requests.length,
          method, url,
          status: '…',
          statusClass: '',
          time: new Date().toISOString().substr(11, 12),
          duration: 0,
          reqHeaders: opts.headers || {},
          reqBody: opts.body || null,
          resHeaders: {},
          resBody: null,
          size: 0,
          error: null,
        };
        self.requests.push(entry);
        if (self.requests.length > self.maxEntries) self.requests.shift();
        self._render();
      } catch (_) { /* never break fetch */ }

      const t0 = performance.now();
      // Call original fetch with proper window context
      return origFetch(...args).then(resp => {
        try {
          if (entry) {
            entry.duration = Math.round(performance.now() - t0);
            entry.status = resp.status;
            entry.statusClass = resp.status >= 500 ? 'net-5xx' : resp.status >= 400 ? 'net-4xx' : resp.status >= 300 ? 'net-3xx' : 'net-2xx';
            const rh = {};
            resp.headers.forEach((v, k) => { rh[k] = v; });
            entry.resHeaders = rh;
            entry.size = parseInt(rh['content-length'] || '0');
            self._render();
          }
        } catch (_) { /* never break fetch */ }
        return resp;
      }).catch(e => {
        try {
          if (entry) {
            entry.duration = Math.round(performance.now() - t0);
            entry.status = 'ERR';
            entry.statusClass = 'net-err';
            entry.error = e.message;
            self._render();
          }
        } catch (_) { /* never break fetch */ }
        throw e;
      });
    };
  }

  _render() {
    const list = this.container.querySelector('#netList');
    let reqs = this.requests;
    if (this.filter) reqs = reqs.filter(r => r.url.toLowerCase().includes(this.filter) || r.method.toLowerCase().includes(this.filter));

    list.innerHTML = reqs.map((r, i) => {
      const sel = r.id === this.selectedIdx ? ' net-selected' : '';
      const urlShort = r.url.length > 60 ? '…' + r.url.slice(-58) : r.url;
      return `<div class="net-row${sel}" data-id="${r.id}">
        <span class="net-method">${r.method}</span>
        <span class="net-status ${r.statusClass}">${r.status}</span>
        <span class="net-url">${_escHtml(urlShort)}</span>
        <span class="net-time">${r.duration}ms</span>
      </div>`;
    }).join('');

    list.querySelectorAll('.net-row').forEach(row => {
      row.onclick = () => { this.selectedIdx = parseInt(row.dataset.id); this._renderDetail(); this._render(); };
    });

    const summary = this.container.querySelector('#netSummary');
    const totalMs = reqs.reduce((s, r) => s + r.duration, 0);
    summary.textContent = `${reqs.length} requests · ${totalMs}ms total`;
  }

  _renderDetail() {
    const detail = this.container.querySelector('#netDetail');
    const r = this.requests.find(r => r.id === this.selectedIdx);
    if (!r) { detail.innerHTML = '<div class="muted" style="padding:12px">Select a request</div>'; return; }

    let html = `<div class="net-detail-section">
      <div class="net-detail-title">${r.method} ${_escHtml(r.url)}</div>
      <div class="net-detail-meta">
        <span class="${r.statusClass}">Status: ${r.status}</span> ·
        <span>${r.duration}ms</span>
        ${r.size ? ` · ${(r.size / 1024).toFixed(1)} KB` : ''}
        ${r.error ? ` · <span class="err">${_escHtml(r.error)}</span>` : ''}
      </div>
    </div>`;

    if (Object.keys(r.reqHeaders).length) {
      html += `<div class="net-detail-section"><div class="net-detail-label">Request Headers</div><pre class="net-headers">`;
      for (const [k, v] of Object.entries(r.reqHeaders)) html += `${_escHtml(k)}: ${_escHtml(String(v))}\n`;
      html += `</pre></div>`;
    }

    if (Object.keys(r.resHeaders).length) {
      html += `<div class="net-detail-section"><div class="net-detail-label">Response Headers</div><pre class="net-headers">`;
      for (const [k, v] of Object.entries(r.resHeaders)) html += `${_escHtml(k)}: ${_escHtml(v)}\n`;
      html += `</pre></div>`;
    }

    detail.innerHTML = html;
  }
}

// ---- DebugSystemPanel ----

class DebugSystemPanel {
  constructor(container, vmId) {
    this.container = container;
    this.vmId = vmId;
    this._timer = null;
    this._fpsHistory = [];
    this._latHistory = [];
    this._memHistory = [];
    this._build();
  }

  _build() {
    this.container.innerHTML = `
      <div class="sys-section">
        <div class="sys-title">Performance</div>
        <div class="sys-charts">
          <div class="sys-chart-box">
            <div class="muted" style="font-size:10px">FPS</div>
            <canvas id="sysFpsChart" width="120" height="36"></canvas>
            <div id="sysFpsVal" class="sys-chart-val">--</div>
          </div>
          <div class="sys-chart-box">
            <div class="muted" style="font-size:10px">Latency</div>
            <canvas id="sysLatChart" width="120" height="36"></canvas>
            <div id="sysLatVal" class="sys-chart-val">--</div>
          </div>
          <div class="sys-chart-box">
            <div class="muted" style="font-size:10px">Memory</div>
            <canvas id="sysMemChart" width="120" height="36"></canvas>
            <div id="sysMemVal" class="sys-chart-val">--</div>
          </div>
        </div>
      </div>
      <div class="sys-section">
        <div class="sys-title">VM Process</div>
        <table class="stats-table" id="sysProcess"></table>
      </div>
      <div class="sys-section">
        <div class="sys-title">Connections</div>
        <table class="stats-table" id="sysConn"></table>
      </div>
      <div class="sys-section">
        <div class="sys-title">Server</div>
        <table class="stats-table" id="sysServer"></table>
      </div>`;
  }

  start(intervalMs = 3000) {
    this._poll();
    this._timer = setInterval(() => this._poll(), intervalMs);
    this._pollServer();
  }

  stop() { if (this._timer) clearInterval(this._timer); }

  async _poll() {
    try {
      const resp = await fetch(`/api/vms/${this.vmId}/metrics`, { credentials: 'same-origin' });
      if (!resp.ok) return;
      const m = await resp.json();

      const fps = m.fps?.current || 0;
      const lat = m.latency?.avg_ms || 0;
      const mem = m.process?.rss_mb || 0;

      this._fpsHistory.push(fps); if (this._fpsHistory.length > 30) this._fpsHistory.shift();
      this._latHistory.push(lat); if (this._latHistory.length > 30) this._latHistory.shift();
      this._memHistory.push(mem); if (this._memHistory.length > 30) this._memHistory.shift();

      this._drawChart('sysFpsChart', this._fpsHistory, '#34d399', 30);
      this._drawChart('sysLatChart', this._latHistory, '#fbbf24', null);
      this._drawChart('sysMemChart', this._memHistory, '#60a5fa', null);

      document.getElementById('sysFpsVal').textContent = fps.toFixed(1) + ' fps';
      document.getElementById('sysLatVal').textContent = lat.toFixed(0) + ' ms';
      document.getElementById('sysMemVal').textContent = mem.toFixed(0) + ' MB';

      // Process table
      const proc = document.getElementById('sysProcess');
      const upSec = m.process?.uptime_sec || 0;
      proc.innerHTML = `
        <tr><td>PID</td><td>${m.process?.pid || '--'}</td></tr>
        <tr><td>RSS</td><td>${mem.toFixed(1)} MB</td></tr>
        <tr><td>CPU</td><td>${(m.process?.cpu_pct || 0).toFixed(1)}%</td></tr>
        <tr><td>Uptime</td><td>${Math.floor(upSec/3600)}h ${Math.floor((upSec%3600)/60)}m</td></tr>
        <tr><td>Frames</td><td>${m.video?.frames_received || 0} recv / ${m.video?.frames_dropped || 0} drop</td></tr>
        <tr><td>FPS</td><td>${fps.toFixed(1)} (avg ${(m.fps?.avg || 0).toFixed(1)}, p95 ${(m.fps?.p95 || 0).toFixed(1)})</td></tr>`;

      // Connections table
      const conn = document.getElementById('sysConn');
      const vidUp = m.video?.uptime_sec || 0;
      const inpUp = m.input?.uptime_sec || 0;
      conn.innerHTML = `
        <tr><td>Video</td><td class="${m.video?.connected ? 'ok' : 'err'}">${m.video?.connected ? '● connected' : '○ disconnected'} (${Math.floor(vidUp/60)}m)</td></tr>
        <tr><td>Input</td><td class="${m.input?.connected ? 'ok' : 'err'}">${m.input?.connected ? '● connected' : '○ disconnected'} (${Math.floor(inpUp/60)}m)</td></tr>
        <tr><td>Commands</td><td>${m.input?.commands_ok || 0} ok / ${m.input?.commands_fail || 0} fail</td></tr>
        <tr><td>Reconnects</td><td>${m.input?.reconnects || 0}</td></tr>`;
    } catch {}
  }

  async _pollServer() {
    try {
      const resp = await fetch('/api/debug/server', { credentials: 'same-origin' });
      if (!resp.ok) return;
      const d = await resp.json();
      const srv = document.getElementById('sysServer');
      if (!srv) return;
      const upSec = d.server?.uptime_sec || 0;
      srv.innerHTML = `
        <tr><td>Host</td><td>${_escHtml(d.host?.hostname || '?')}</td></tr>
        <tr><td>Platform</td><td>${_escHtml(d.host?.platform || '?')} · ${d.host?.cpu_count || '?'} cores</td></tr>
        <tr><td>Python</td><td>${_escHtml(d.server?.python_version || '?')}</td></tr>
        <tr><td>Uptime</td><td>${Math.floor(upSec/3600)}h ${Math.floor((upSec%3600)/60)}m</td></tr>
        <tr><td>VMs</td><td>${d.vms?.running || 0} running / ${d.vms?.total || 0} total</td></tr>
        <tr><td>RAM</td><td>${d.ram?.used_mb || 0} / ${d.ram?.budget_mb || 0} MB</td></tr>
        <tr><td>Storage</td><td>${d.storage?.vms_dir_size_mb || 0} MB</td></tr>
        <tr><td>WebRTC</td><td>${d.webrtc?.active_connections || 0} active</td></tr>`;
    } catch {}
  }

  _drawChart(canvasId, data, color, maxVal) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (data.length < 2) return;

    const max = maxVal || Math.max(...data, 1);
    const step = w / (data.length - 1);

    // Fill
    ctx.beginPath();
    ctx.moveTo(0, h);
    data.forEach((v, i) => ctx.lineTo(i * step, h - (v / max) * h));
    ctx.lineTo((data.length - 1) * step, h);
    ctx.closePath();
    ctx.fillStyle = color + '18';
    ctx.fill();

    // Line
    ctx.beginPath();
    data.forEach((v, i) => { if (i === 0) ctx.moveTo(0, h - (v / max) * h); else ctx.lineTo(i * step, h - (v / max) * h); });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}
