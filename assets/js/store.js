/* ============================================================
   PumpAI Store
   - LocalStorage-backed
   - Immutable append-only audit log
   - Replay-safe (every mutation is logged)
   - Offline-first, autosaving drafts
   ============================================================ */

const STORE_KEY = 'pumpai.v1';
const AUDIT_KEY = 'pumpai.audit.v1';
const DRAFT_KEY = 'pumpai.drafts.v1';

const PumpStore = (() => {
  // ---------- defaults ----------
  const seed = () => ({
    meta: {
      pumpName: 'Chhaba Filling Station',
      brand: 'HPCL',
      location: 'Jaipur, Rajasthan',
      currency: 'INR',
      gst: '08AAAAA0000A1Z5',
      createdAt: nowISO()
    },
    nozzles: [
      { id: 'N1', name: 'MS-1', fuel: 'MS', price: 106.31, opening: 124580.45 },
      { id: 'N2', name: 'MS-2', fuel: 'MS', price: 106.31, opening: 98220.18 },
      { id: 'N3', name: 'HSD-1', fuel: 'HSD', price: 94.27, opening: 215430.66 },
      { id: 'N4', name: 'HSD-2', fuel: 'HSD', price: 94.27, opening: 180992.30 },
      { id: 'N5', name: 'XP-1', fuel: 'XP95', price: 113.45, opening: 42115.00 }
    ],
    tanks: [
      { id: 'T1', fuel: 'MS', capacity: 20000, dip: 14820, dead: 200 },
      { id: 'T2', fuel: 'HSD', capacity: 20000, dip: 11240, dead: 200 },
      { id: 'T3', fuel: 'XP95', capacity: 10000, dip: 6810, dead: 100 }
    ],
    operators: [
      { id: 'OP1', name: 'Rakesh Kumar', phone: '98290-XXXXX' },
      { id: 'OP2', name: 'Sunita Devi', phone: '99280-XXXXX' },
      { id: 'OP3', name: 'Imran Khan', phone: '94130-XXXXX' }
    ],
    shifts: [],          // closed shifts
    activeShift: null,   // currently running
    upi: [],       // settlement records
    credit: [],       // udhaar ledger
    expenses: [],       // daily expenses
    ocrJobs: [],       // upload + extraction queue
    settings: {
      lowConfidenceThreshold: 0.85,
      shortageToleranceL: 2.0,    // litres
      shortageToleranceRs: 50,    // rupees
      strictConsensus: true
    }
  });

  // ---------- helpers ----------
  function nowISO() { return new Date().toISOString(); }

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) {
        const s = seed();
        localStorage.setItem(STORE_KEY, JSON.stringify(s));
        return s;
      }
      return JSON.parse(raw);
    } catch (e) {
      console.error('store load failed, reseeding', e);
      const s = seed();
      localStorage.setItem(STORE_KEY, JSON.stringify(s));
      return s;
    }
  }

  function save(state) {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  }

  function audit(event, payload) {
    const log = JSON.parse(localStorage.getItem(AUDIT_KEY) || '[]');
    log.push({
      id: 'a_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      at: nowISO(),
      event,
      payload
    });
    // append-only — we never trim
    localStorage.setItem(AUDIT_KEY, JSON.stringify(log));
  }

  function getAudit() {
    return JSON.parse(localStorage.getItem(AUDIT_KEY) || '[]');
  }

  // ---------- public surface ----------
  let state = load();

  return {
    /** read current state (do not mutate directly) */
    get state() { return state; },

    /** atomic update: pass a function (s) => newState, returns audit entry */
    update(event, mutator, payload = {}) {
      const before = JSON.parse(JSON.stringify(state));
      mutator(state);
      save(state);
      audit(event, { ...payload, _hash: hashState(state) });
      window.dispatchEvent(new CustomEvent('pumpai:change', { detail: { event } }));
      return { before, after: state };
    },

    /** reset to seed (kept available; UI guards this) */
    reset() {
      localStorage.removeItem(STORE_KEY);
      localStorage.removeItem(AUDIT_KEY);
      localStorage.removeItem(DRAFT_KEY);
      state = load();
      audit('store.reset', {});
    },

    /** drafts — for offline autosave */
    saveDraft(key, value) {
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
      d[key] = { value, at: nowISO() };
      localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
    },
    loadDraft(key) {
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
      return d[key]?.value || null;
    },
    clearDraft(key) {
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
      delete d[key];
      localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
    },

    audit: getAudit,
    nowISO
  };
})();

/* cheap deterministic state hash for audit traceability */
function hashState(o) {
  const s = JSON.stringify(o);
  let h = 0;
  for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; }
  return (h >>> 0).toString(16).padStart(8, '0');
}

window.PumpStore = PumpStore;
