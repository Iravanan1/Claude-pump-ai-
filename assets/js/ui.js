/* ============================================================
   PumpAI UI shell
   - renders sidebar nav
   - toast system
   - small DOM helpers
   ============================================================ */

const UI = (() => {

  const NAV = [
    { group: 'Pump Operations', items: [
      { href: 'ocr-upload.html',        label: 'Scan Register',      icon: '⬆' },
      { href: 'review-queue.html',       label: 'Review Queue',       icon: '⚠️' },
      { href: 'error-resolution.html',   label: 'Error Resolution',   icon: '🔧' },
      { href: 'historical-log.html',     label: 'Historical Log',     icon: '⊡' },
      { href: 'bulk-editor.html',        label: 'Bulk Editor',        icon: '▦' },
      { href: 'discount-manager.html',   label: 'Special Contracts',  icon: '📋' },
      { href: 'purchase-registry.html',  label: 'Purchase Rates',     icon: '💰' },
      { href: 'sales-target.html',       label: 'Sales Targets',      icon: '📈' },
      { href: 'log-viewer.html',         label: 'Diagnostics',        icon: '⚙️' }
    ]}
  ];

  function currentPage() {
    const p = location.pathname.split('/').pop();
    return p === '' ? 'index.html' : p;
  }

  function renderShell(opts = {}) {
    const here = currentPage();
    const nav = NAV.map(g => `
      <div class="nav-group">
        <div class="nav-label">${g.group}</div>
        ${g.items.map(it => `
          <a class="nav-item ${it.href === here ? 'active' : ''}"
             href="${pagePath(it.href)}">
            <span class="nav-dot"></span>
            <span style="flex:1">${it.label}</span>
          </a>
        `).join('')}
      </div>
    `).join('');

    const sidebar = `
      <aside class="sidebar">
        <a class="brand" href="${pagePath('index.html')}" style="text-decoration:none">
          <div class="brand-mark">P</div>
          <div>
            <div class="brand-name">PumpAI</div>
            <div class="brand-sub">${PumpStore.state.meta.brand} · v1.0</div>
          </div>
        </a>
        ${nav}
        <div class="nav-group">
          <div class="nav-label">Status</div>
          <div style="padding: 4px 10px; font-size: 12px; color: var(--ink-muted);">
            <div class="row" style="gap:6px; display:flex; flex-direction:column; align-items:flex-start;">
              <span class="badge ok"><span class="dot"></span>Online</span>
              <span id="usb-sync-status" class="badge ok" style="display:none; margin-top: 6px; font-size: 11px; line-height: 1.2;">
                <span class="dot"></span>
                Local Physical Redundancy Verified - USB Mirror Complete
              </span>
            </div>
            <div style="margin-top:8px; font-size:11px;">
              ${PumpStore.state.meta.pumpName}<br>
              <span style="color: var(--ink-faint);">${PumpStore.state.meta.location}</span>
            </div>
          </div>
        </div>
      </aside>`;

    document.body.classList.add('app');
    const existing = document.querySelector('aside.sidebar');
    if (!existing) {
      document.body.insertAdjacentHTML('afterbegin', sidebar);
    }

    // ensure toast host exists
    if (!document.querySelector('.toast-host')) {
      const t = document.createElement('div');
      t.className = 'toast-host';
      document.body.appendChild(t);
    }

    // Connect to USB sync event stream
    connectUsbSyncEvents();
  }

  /** Pages live under /pages, dashboard at root. Adjust links so they work
   *  regardless of which file you opened.
   */
  function pagePath(href) {
    if (href === 'index.html') {
      // if we're already inside /pages, go up one
      if (location.pathname.includes('/pages/')) return '../index.html';
      return 'index.html';
    }
    if (location.pathname.includes('/pages/')) return href;
    return 'pages/' + href;
  }

  function toast(msg, type = '') {
    const host = document.querySelector('.toast-host');
    if (!host) return;
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity 180ms';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 200);
    }, 2400);
  }

  function pageHead({ crumb, title, desc, actions = '' }) {
    return `
      <div class="page-head">
        <div>
          <div class="crumbs">${crumb}</div>
          <h1>${title}</h1>
          ${desc ? `<div class="desc">${desc}</div>` : ''}
        </div>
        <div class="head-actions" style="display: flex; align-items: center; gap: 12px;">
          <div id="active-profile-container" style="display: inline-block;"></div>
          ${actions}
        </div>
      </div>`;
  }

  function confidencePill(conf) {
    const pct = Math.round(conf * 100);
    const cls = conf >= 0.9 ? 'high' : conf >= 0.75 ? 'mid' : 'low';
    return `<span class="conf ${cls}">
      <span class="conf-bar"><span style="width:${pct}%"></span></span>
      ${pct}%
    </span>`;
  }

  function emptyState({ icon = '◌', title, body }) {
    return `<div class="empty">
      <div class="empty-icon">${icon}</div>
      <h3>${title}</h3>
      ${body ? `<div>${body}</div>` : ''}
    </div>`;
  }

  // confirm dialog (uses native confirm for simplicity — keep UX minimal)
  function confirmAction(msg) { return window.confirm(msg); }

  function connectUsbSyncEvents() {
    if (window.usbSyncEventSource) {
      return;
    }
    const protocol = location.protocol;
    const hostname = location.hostname === 'localhost' || location.hostname === '127.0.0.1' ? 'localhost:8000' : location.host;
    const sseUrl = `${protocol}//${hostname}/api/usb-sync/events`;
    
    try {
      const source = new EventSource(sseUrl);
      window.usbSyncEventSource = source;
      
      source.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.status === 'success' && data.event === 'usb_sync_complete') {
            const el = document.getElementById('usb-sync-status');
            if (el) {
              el.style.display = 'inline-flex';
            }
          }
        } catch (parseErr) {
          console.error("Failed to parse USB Sync event:", parseErr);
        }
      };
      
      source.onerror = () => {
        // EventSource will automatically retry connecting
      };
    } catch (e) {
      console.error("Failed to initialize USB Sync EventSource:", e);
    }
  }

  return { renderShell, toast, pageHead, pagePath, confidencePill, emptyState, confirmAction, connectUsbSyncEvents };
})();

window.UI = UI;

// boot — every page calls this once
window.addEventListener('DOMContentLoaded', () => {
  UI.renderShell();
  syncActiveProfileDropdown();
});

let activeProfileCache = null;

async function syncActiveProfileDropdown() {
  const targets = [];
  
  const phContainer = document.getElementById('active-profile-container');
  if (phContainer && !phContainer.querySelector('.active-profile-selector')) {
    targets.push(phContainer);
  }
  
  const sysBar = document.getElementById('system-bar');
  if (sysBar && !sysBar.querySelector('.active-profile-selector')) {
    const statusRow = sysBar.querySelector('.sys-status-row');
    if (statusRow) {
      const wrapper = document.createElement('div');
      wrapper.id = 'active-profile-system-bar-container';
      statusRow.prepend(wrapper);
      targets.push(wrapper);
    }
  }
  
  const ervHeader = document.querySelector('.erv-header-right');
  if (ervHeader && !ervHeader.querySelector('.active-profile-selector')) {
    const wrapper = document.createElement('div');
    wrapper.id = 'active-profile-erv-container';
    ervHeader.prepend(wrapper);
    targets.push(wrapper);
  }
  
  if (targets.length === 0) return;
  
  if (!activeProfileCache) {
    try {
      const protocol = location.protocol;
      const hostname = location.hostname === 'localhost' || location.hostname === '127.0.0.1' ? 'localhost:8000' : location.host;
      const res = await fetch(`${protocol}//${hostname}/api/system/active-workspace`);
      if (res.ok) {
        const data = await res.json();
        activeProfileCache = data.active_profile || 'pump_station_1';
      } else {
        activeProfileCache = 'pump_station_1';
      }
    } catch (e) {
      console.error("Failed to fetch active workspace:", e);
      activeProfileCache = 'pump_station_1';
    }
  }
  
  targets.forEach(tgt => {
    tgt.innerHTML = `
      <div class="active-profile-selector" style="display: inline-flex; align-items: center; gap: 8px; font-family: inherit; font-size: 12px; color: var(--ink-soft); vertical-align: middle;">
        <label style="font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; color: var(--ink-muted); cursor: pointer; user-select: none;">Active Profile</label>
        <select class="active-profile-dropdown" style="background: var(--bg-soft, #111827); border: 1px solid var(--border-soft, #1E2736); color: var(--ink, #E5E7EB); border-radius: 4px; padding: 4px 8px; font-family: inherit; font-size: 12px; cursor: pointer; outline: none;">
          <option value="pump_station_1" ${activeProfileCache === 'pump_station_1' ? 'selected' : ''}>Pump Station 1</option>
          <option value="pump_station_2" ${activeProfileCache === 'pump_station_2' ? 'selected' : ''}>Pump Station 2</option>
        </select>
      </div>
    `;
    
    const select = tgt.querySelector('.active-profile-dropdown');
    if (select) {
      select.addEventListener('change', async (e) => {
        const newProfile = e.target.value;
        UI.toast(`Switching workspace to '${newProfile}'...`, 'info');
        try {
          const protocol = location.protocol;
          const hostname = location.hostname === 'localhost' || location.hostname === '127.0.0.1' ? 'localhost:8000' : location.host;
          const res = await fetch(`${protocol}//${hostname}/api/system/switch-workspace`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_name: newProfile })
          });
          if (res.ok) {
            UI.toast(`✓ Workspace profile switched!`, 'ok');
            setTimeout(() => {
              window.location.reload();
            }, 500);
          } else {
            const err = await res.json();
            UI.toast(`Failed to switch profile: ${err.detail || res.statusText}`, 'bad');
          }
        } catch (err) {
          console.error("Failed to switch workspace:", err);
          UI.toast(`Error switching profile: ${err.message}`, 'bad');
        }
      });
    }
  });
}

setInterval(syncActiveProfileDropdown, 500);
