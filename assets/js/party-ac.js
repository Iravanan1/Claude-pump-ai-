/**
 * PartyAutoComplete — PumpAI
 * ============================================================
 * Lightweight, keyboard-navigable party-name auto-suggest
 * widget that can be attached to any <input> element.
 *
 * Usage:
 *   PartyAC.attach(inputElement)     — attach once after DOM creation
 *   PartyAC.attachAll(selector)      — attach to every matching input
 *   PartyAC.detach(inputElement)     — remove widget from input
 *
 * The floating dropdown is appended to <body> and repositioned on
 * each focus/keystroke so it always anchors below the active cell.
 *
 * Keyboard controls (when dropdown is open):
 *   ArrowDown / ArrowUp  — move highlight
 *   Enter / Tab          — select highlighted item
 *   Escape               — close without selecting
 *   Any other key        — filter & re-query
 * ============================================================
 */
(function (global) {
  'use strict';

  // -----------------------------------------------------------------------
  // Config
  // -----------------------------------------------------------------------
  const API_BASE    = 'http://localhost:8000';
  const ENDPOINT    = '/api/ledger/suggest-parties';
  const LIMIT       = 12;
  const MIN_QUERY_LEN = 0;    // show top-N on focus even with empty field
  const DEBOUNCE_MS = 90;     // ms to wait after last keystroke before fetching

  // -----------------------------------------------------------------------
  // Singleton DOM element — one dropdown shared across all inputs
  // -----------------------------------------------------------------------
  let _dropdown  = null;   // the floating <ul>
  let _activeInput = null; // the <input> currently owning the dropdown
  let _items     = [];     // current suggestion strings
  let _hiIdx     = -1;     // highlighted index (-1 = none)
  let _debounceTimer = null;
  let _fetchController = null;

  function _ensureDropdown() {
    if (_dropdown) return;

    _dropdown = document.createElement('ul');
    _dropdown.id = 'pac-dropdown';
    _dropdown.setAttribute('role', 'listbox');
    _dropdown.setAttribute('aria-label', 'Party name suggestions');

    Object.assign(_dropdown.style, {
      position:        'fixed',
      zIndex:          '99999',
      margin:          '0',
      padding:         '4px 0',
      listStyle:       'none',
      background:      '#111827',            // --bg-elev
      border:          '1px solid #374151',  // --border
      borderTop:       '2px solid #F59E0B',  // --accent top accent line
      borderRadius:    '0 0 4px 4px',
      boxShadow:       '0 8px 24px rgba(0,0,0,0.55)',
      minWidth:        '200px',
      maxWidth:        '420px',
      maxHeight:       '260px',
      overflowY:       'auto',
      fontFamily:      "'JetBrains Mono', 'Courier New', monospace",
      fontSize:        '13px',
      display:         'none',
      scrollbarWidth:  'thin',
      scrollbarColor:  '#374151 #0B0F19',
    });

    // Mouse-select an item
    _dropdown.addEventListener('mousedown', (e) => {
      const li = e.target.closest('li[data-idx]');
      if (!li) return;
      e.preventDefault();
      _selectIdx(parseInt(li.dataset.idx));
    });

    document.body.appendChild(_dropdown);

    // Close on any outside click
    document.addEventListener('mousedown', (e) => {
      if (!_dropdown.contains(e.target) && e.target !== _activeInput) {
        _close();
      }
    }, true);

    // Reposition on scroll / resize
    window.addEventListener('scroll', _reposition, true);
    window.addEventListener('resize', _reposition);
  }

  // -----------------------------------------------------------------------
  // Position the dropdown directly under the active input cell
  // -----------------------------------------------------------------------
  function _reposition() {
    if (!_activeInput || _dropdown.style.display === 'none') return;
    const r = _activeInput.getBoundingClientRect();
    Object.assign(_dropdown.style, {
      top:      (r.bottom) + 'px',
      left:     r.left + 'px',
      minWidth: Math.max(r.width, 200) + 'px',
    });
  }

  // -----------------------------------------------------------------------
  // Render dropdown list items
  // -----------------------------------------------------------------------
  function _render(query) {
    _dropdown.innerHTML = '';
    _hiIdx = -1;

    if (_items.length === 0) {
      _close();
      return;
    }

    const qLower = (query || '').trim().toLowerCase();

    _items.forEach((name, idx) => {
      const li = document.createElement('li');
      li.dataset.idx = idx;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', 'false');

      // Bold the matched portion
      let display = _escHtml(name);
      if (qLower && name.toLowerCase().includes(qLower)) {
        const start = name.toLowerCase().indexOf(qLower);
        display = _escHtml(name.slice(0, start))
          + '<span style="color:#F59E0B;font-weight:700;">' + _escHtml(name.slice(start, start + qLower.length)) + '</span>'
          + _escHtml(name.slice(start + qLower.length));
      }

      li.innerHTML = display;

      Object.assign(li.style, {
        padding:    '7px 14px',
        cursor:     'pointer',
        color:      '#E5E7EB',
        lineHeight: '1.4',
        whiteSpace: 'nowrap',
        overflow:   'hidden',
        textOverflow: 'ellipsis',
        borderBottom: '1px solid #1F2937',
      });

      li.addEventListener('mouseover', () => _setHighlight(idx));

      _dropdown.appendChild(li);
    });

    _dropdown.style.display = 'block';
    _reposition();
  }

  // -----------------------------------------------------------------------
  // Highlight management
  // -----------------------------------------------------------------------
  function _setHighlight(idx) {
    const lis = _dropdown.querySelectorAll('li');
    lis.forEach((li, i) => {
      const active = i === idx;
      li.style.background = active ? '#1F2937' : 'transparent';
      li.style.color       = active ? '#F59E0B' : '#E5E7EB';
      li.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    _hiIdx = idx;
    // Scroll highlighted item into view
    if (lis[idx]) lis[idx].scrollIntoView({ block: 'nearest' });
  }

  // -----------------------------------------------------------------------
  // Select a specific item index
  // -----------------------------------------------------------------------
  function _selectIdx(idx) {
    if (idx < 0 || idx >= _items.length) return;
    const chosen = _items[idx];
    if (_activeInput) {
      _activeInput.value = chosen;
      // Fire both 'input' and 'change' events so outer grid listeners update
      _activeInput.dispatchEvent(new Event('input',  { bubbles: true }));
      _activeInput.dispatchEvent(new Event('change', { bubbles: true }));
    }
    _close();
  }

  // -----------------------------------------------------------------------
  // Close / hide the dropdown
  // -----------------------------------------------------------------------
  function _close() {
    if (_dropdown) _dropdown.style.display = 'none';
    _hiIdx = -1;
    _items = [];
  }

  // -----------------------------------------------------------------------
  // Fetch suggestions from backend
  // -----------------------------------------------------------------------
  async function _fetchSuggestions(q) {
    if (_fetchController) _fetchController.abort();
    _fetchController = new AbortController();

    try {
      const url = `${API_BASE}${ENDPOINT}?q=${encodeURIComponent(q)}&limit=${LIMIT}`;
      const res = await fetch(url, { signal: _fetchController.signal });
      if (!res.ok) return;
      const data = await res.json();
      _items = data.suggestions || [];
      _render(q);
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.debug('[PartyAC] fetch failed silently:', err.message);
      }
    }
  }

  // -----------------------------------------------------------------------
  // Debounced trigger
  // -----------------------------------------------------------------------
  function _trigger(input) {
    clearTimeout(_debounceTimer);
    const q = input.value.trim();
    _debounceTimer = setTimeout(() => _fetchSuggestions(q), DEBOUNCE_MS);
  }

  // -----------------------------------------------------------------------
  // Keyboard handler (wired onto the input element)
  // -----------------------------------------------------------------------
  function _onKeyDown(e) {
    const visible = _dropdown && _dropdown.style.display !== 'none';

    if (e.key === 'ArrowDown') {
      if (!visible) { _trigger(e.target); return; }
      e.preventDefault();
      _setHighlight(Math.min(_hiIdx + 1, _items.length - 1));
      return;
    }
    if (e.key === 'ArrowUp') {
      if (!visible) return;
      e.preventDefault();
      _setHighlight(Math.max(_hiIdx - 1, 0));
      return;
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (visible && _hiIdx >= 0) {
        e.preventDefault();
        _selectIdx(_hiIdx);
        return;
      }
      // No highlight — just close and let Enter propagate
      _close();
      return;
    }
    if (e.key === 'Escape') {
      _close();
      return;
    }
    // NumpadEnter — do not prevent (allows form commit hotkey to still fire)
    if (e.code === 'NumpadEnter') {
      if (visible && _hiIdx >= 0) {
        e.preventDefault();
        _selectIdx(_hiIdx);
        return;
      }
      _close();
    }
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------
  function attach(input) {
    if (!input || input._pacAttached) return;
    _ensureDropdown();

    input._pacAttached = true;

    input.addEventListener('focus', () => {
      _activeInput = input;
      _trigger(input);
    });

    input.addEventListener('input', () => {
      _activeInput = input;
      _trigger(input);
    });

    input.addEventListener('keydown', _onKeyDown);

    input.addEventListener('blur', () => {
      // Short delay so mousedown on dropdown fires before blur hides it
      setTimeout(() => {
        if (document.activeElement !== _activeInput) {
          _close();
        }
      }, 150);
    });
  }

  function detach(input) {
    if (!input || !input._pacAttached) return;
    input.removeEventListener('input', _trigger);
    input.removeEventListener('keydown', _onKeyDown);
    input._pacAttached = false;
  }

  /**
   * Attach to every input matching the given CSS selector.
   * Call after the DOM elements have been inserted.
   */
  function attachAll(selector) {
    document.querySelectorAll(selector).forEach(el => attach(el));
  }

  global.PartyAC = { attach, detach, attachAll };

})(window);
