/* ============================================================
   PumpAI Accounting
   - Deterministic, replay-safe calculations
   - No floating-point surprises (we round at presentation only)
   - Anomaly & shortage detection
   - Never mutates store directly
   ============================================================ */

const Acct = (() => {

  /** banker-safe paise math */
  const paise = rs => Math.round(rs * 100);
  const rupees = p => p / 100;

  /** litre math at 3dp; meters typically read to 2dp on totaliser */
  const round3 = n => Math.round(n * 1000) / 1000;
  const round2 = n => Math.round(n * 100) / 100;

  /** -------- Nozzle line: closing - opening = sold litres -------- */
  function nozzleSale({ opening, closing, testing = 0, price }) {
    // testing returns are removed from sales
    const litresGross = round3(closing - opening);
    const litres = round3(Math.max(0, litresGross - testing));
    const amountPaise = paise(litres * price);
    return {
      litresGross,
      testing,
      litres,
      price,
      amountPaise,
      amountRs: rupees(amountPaise)
    };
  }

  /** -------- Shift totals -------- */
  function shiftTotals(shift) {
    const rows = (shift.readings || []).map(r => {
      const n = (PumpStore.state.nozzles || []).find(x => x.id === r.nozzleId) || {};
      return {
        nozzleId: r.nozzleId,
        name: n.name,
        fuel: n.fuel,
        ...nozzleSale({
          opening: Number(r.opening || 0),
          closing: Number(r.closing || 0),
          testing: Number(r.testing || 0),
          price:   Number(r.price ?? n.price ?? 0)
        })
      };
    });

    const totals = rows.reduce((a, r) => ({
      litres: round3(a.litres + r.litres),
      amountPaise: a.amountPaise + r.amountPaise
    }), { litres: 0, amountPaise: 0 });

    // Tenders (what came in)
    const t = shift.tenders || {};
    const cashRs   = Number(t.cash   || 0);
    const upiRs    = Number(t.upi    || 0);
    const cardRs   = Number(t.card   || 0);
    const creditRs = Number(t.credit || 0);
    const fleetRs  = Number(t.fleet  || 0);
    const collectedPaise = paise(cashRs + upiRs + cardRs + creditRs + fleetRs);

    // Expenses paid out of till during shift
    const expRs = (shift.expenses || []).reduce((s, e) => s + Number(e.amount || 0), 0);
    const expPaise = paise(expRs);

    const expectedPaise = totals.amountPaise;
    // Effective collected = tenders + expenses paid out (since cash drawer paid them)
    const effectivePaise = collectedPaise + expPaise;
    const variancePaise  = effectivePaise - expectedPaise;

    return {
      rows,
      sales: {
        litres: totals.litres,
        amountRs: rupees(totals.amountPaise),
        amountPaise: totals.amountPaise
      },
      tenders: {
        cash: cashRs, upi: upiRs, card: cardRs,
        credit: creditRs, fleet: fleetRs,
        totalRs: rupees(collectedPaise)
      },
      expenses: { totalRs: expRs },
      expectedRs: rupees(expectedPaise),
      collectedRs: rupees(effectivePaise),
      varianceRs: rupees(variancePaise),
      status: classifyVariance(variancePaise)
    };
  }

  function classifyVariance(variancePaise) {
    const tolPaise = paise(PumpStore.state.settings.shortageToleranceRs || 0);
    if (Math.abs(variancePaise) <= tolPaise) return 'balanced';
    return variancePaise < 0 ? 'short' : 'excess';
  }

  /** -------- Wetstock reconciliation per tank --------
   * book = opening_dip + receipts - sales(by fuel) - testing
   * variance_L = actual_dip - book
   */
  function wetstock(tank, ctx) {
    const { openingDip, receipts = 0, salesL = 0, testingL = 0, closingDip } = ctx;
    const book = round3(openingDip + receipts - salesL - testingL);
    const variance = round3(closingDip - book);
    const tol = PumpStore.state.settings.shortageToleranceL || 0;
    const status = Math.abs(variance) <= tol ? 'ok' : (variance < 0 ? 'short' : 'excess');
    return { tankId: tank.id, fuel: tank.fuel, openingDip, receipts, salesL, testingL, closingDip, book, variance, status };
  }

  /** -------- UPI settlement reconciliation -------- */
  function reconcileUpi(declaredRs, settlementRecords) {
    const settledPaise = settlementRecords.reduce((s, r) => s + paise(Number(r.amount || 0)), 0);
    const declaredPaise = paise(Number(declaredRs || 0));
    const diffPaise = settledPaise - declaredPaise;
    const tolPaise = paise(PumpStore.state.settings.shortageToleranceRs);
    const status = Math.abs(diffPaise) <= tolPaise ? 'matched' : (diffPaise < 0 ? 'short' : 'excess');
    return {
      declaredRs,
      settledRs: rupees(settledPaise),
      diffRs: rupees(diffPaise),
      status,
      count: settlementRecords.length
    };
  }

  /** -------- Anomaly detector for a shift -------- */
  function anomalies(shift) {
    const t = shiftTotals(shift);
    const out = [];

    if (t.status === 'short')  out.push({ level: 'bad',  msg: `Cash short by ₹${Math.abs(t.varianceRs).toFixed(2)}` });
    if (t.status === 'excess') out.push({ level: 'warn', msg: `Cash excess of ₹${t.varianceRs.toFixed(2)}` });

    // Negative readings
    t.rows.forEach(r => {
      if (r.litresGross < 0)
        out.push({ level: 'bad', msg: `${r.name}: closing reading is less than opening (impossible)` });
      if (r.litres > 5000)
        out.push({ level: 'warn', msg: `${r.name}: unusually high sale of ${r.litres.toFixed(2)}L` });
    });

    // Duplicate nozzleIds in readings
    const seen = new Set();
    (shift.readings || []).forEach(r => {
      if (seen.has(r.nozzleId)) out.push({ level: 'bad', msg: `Duplicate reading for nozzle ${r.nozzleId}` });
      seen.add(r.nozzleId);
    });

    return out;
  }

  /** -------- Carry-forward validation --------
   * Closing reading of shift N for nozzle X must equal opening of shift N+1.
   */
  function validateCarryForward(prevShift, nextShift) {
    const issues = [];
    if (!prevShift || !nextShift) return issues;
    const prevMap = new Map((prevShift.readings || []).map(r => [r.nozzleId, r.closing]));
    (nextShift.readings || []).forEach(r => {
      const prevClose = prevMap.get(r.nozzleId);
      if (prevClose != null && Math.abs(prevClose - r.opening) > 0.005) {
        issues.push({
          level: 'bad',
          msg: `Carry-forward break on ${r.nozzleId}: prev closing ${prevClose} ≠ opening ${r.opening}`
        });
      }
    });
    return issues;
  }

  return {
    paise, rupees, round2, round3,
    nozzleSale, shiftTotals,
    wetstock, reconcileUpi,
    anomalies, validateCarryForward
  };
})();

window.Acct = Acct;

/* shorthand formatters */
window.fmt = {
  rs(n)  { const v = Number(n || 0); return '₹' + v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
  L(n)   { return Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + ' L'; },
  num(n, d=2) { return Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }); },
  date(iso) { try { return new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }); } catch { return iso; } },
  shortDate(iso) { try { return new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }); } catch { return iso; } }
};
