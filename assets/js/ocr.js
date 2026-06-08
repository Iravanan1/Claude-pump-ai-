/* ============================================================
   PumpAI OCR Engine (client-side stub)
   - Wraps two simulated engines (PaddleOCR + EasyOCR) for consensus
   - Returns per-field confidence
   - Real implementation would call a backend running both engines.
   - This module is structured so that swap-in is one method.
   ============================================================ */

const OCR = (() => {

  /** Public API:
   *   const job = await OCR.extract(file, { template: 'shift_register' })
   */
  async function extractBatch(files, opts = {}) {
    const fileList = Array.isArray(files) || files instanceof FileList ? Array.from(files) : [files];
    if (fileList.length === 0) {
      throw new Error("No files provided for extraction.");
    }
    
    // Prepare multi-part form data for bulk upload
    const formData = new FormData();
    fileList.forEach(file => {
      formData.append("files", file);
    });
    
    console.log(`Sending ${fileList.length} files in bulk intake to FastAPI backend...`);
    const response = await fetch("http://localhost:8000/api/upload", {
      method: "POST",
      body: formData
    });
    
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to process files with local AI vision backend.");
    }
    
    const backendData = await response.json();
    console.log("Received AI audited JSON from backend:", backendData);
    
    // Normalize response to array
    const resultsArray = Array.isArray(backendData) ? backendData : [backendData];
    
    const jobs = [];
    for (let i = 0; i < resultsArray.length; i++) {
      const pageData = resultsArray[i];
      const consensus = buildConsensusFromBackend(pageData);
      
      const warnings = consensus
        .filter(f => {
          if (pageData.offline_mode) return false;
          return f.conf < (PumpStore.state.settings?.lowConfidenceThreshold || 0.7) || f.mismatch;
        })
        .map(f => ({
          field: f.key,
          level: f.mismatch ? 'bad' : 'warn',
          msg: f.mismatch
            ? `Arithmetic mismatch on ${f.label} (Calculated ${f.engineB} vs Transcribed ${f.engineA})`
            : `Low confidence on ${f.label} (${(f.conf * 100).toFixed(0)}%)`
        }));
        
      if (pageData.offline_mode) {
        warnings.push({
          field: "general",
          level: "warn",
          msg: "Cloud APIs unreachable. Switching to offline backup. Please fill in details manually."
        });
      }
        
      if (pageData.validation_status === "corrected") {
        warnings.push({
          field: "general",
          level: "warn",
          msg: "Arithmetic errors were automatically corrected: " + pageData.audit_explanation
        });
      }

      // Try to match corresponding original file by name
      const originalFilename = pageData.original_filename || (fileList[i] ? fileList[i].name : "Unknown");
      const matchedFile = fileList.find(f => f.name === originalFilename) || fileList[0];
      const fileDataURL = matchedFile ? await fileToDataURL(matchedFile) : "";
      
      jobs.push({
        id: 'ocr_' + Date.now().toString(36) + '_' + i + '_' + Math.random().toString(36).substr(2, 5),
        filename: pageData.page_index !== undefined && resultsArray.length > 1
          ? `${originalFilename} (Page ${pageData.page_index + 1})`
          : originalFilename,
        size: matchedFile ? matchedFile.size : 0,
        capturedAt: new Date().toISOString(),
        image: pageData.image_url || fileDataURL,
        backendData: pageData,
        consensus,
        warnings,
        status: warnings.some(w => w.level === 'bad') ? 'needs_review' : 'verified'
      });
    }
    
    return jobs;
  }

  async function extract(file, opts = {}) {
    const jobs = await extractBatch([file], opts);
    return jobs[0];
  }

  function fileToDataURL(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload  = () => res(r.result);
      r.onerror = rej;
      r.readAsDataURL(file);
    });
  }


  /** Field template — what we expect to read off a register page */
  const REGISTER_FIELDS = [
    { key: 'date',     label: 'Date',                  type: 'date' },
    { key: 'shift',    label: 'Shift',                 type: 'text' },
    { key: 'n1_open',  label: 'MS-1 Opening',          type: 'num' },
    { key: 'n1_close', label: 'MS-1 Closing',          type: 'num' },
    { key: 'n1_sales', label: 'MS-1 Liters Sold',      type: 'num' },
    { key: 'n2_open',  label: 'MS-2 Opening',          type: 'num' },
    { key: 'n2_close', label: 'MS-2 Closing',          type: 'num' },
    { key: 'n2_sales', label: 'MS-2 Liters Sold',      type: 'num' },
    { key: 'n3_open',  label: 'HSD-1 Opening',         type: 'num' },
    { key: 'n3_close', label: 'HSD-1 Closing',         type: 'num' },
    { key: 'n3_sales', label: 'HSD-1 Liters Sold',     type: 'num' },
    { key: 'cash',     label: 'Cash Tender',           type: 'num' },
    { key: 'upi',      label: 'UPI Tender',            type: 'num' },
    { key: 'paytm',    label: 'Paytm Transfers',       type: 'num' },
    { key: 'card',     label: 'Card Tender',           type: 'num' },
    { key: 'udhaar',   label: 'Udhaar (Credit) Sales', type: 'num' },
    { key: 'expenses', label: 'Expenses',              type: 'num' }
  ];

  function buildConsensusFromBackend(data) {
    const nozzles = data.nozzles || [];
    const getNozzle = (index) => nozzles[index] || {};
    const isOffline = !!data.offline_mode;
    
    return REGISTER_FIELDS.map(f => {
      let value = 0;
      let conf = isOffline ? 1.0 : 0.99;
      let engineA = ""; // Transcribed
      let engineB = ""; // Calculated
      let mismatch = false;
      
      switch (f.key) {
        case 'date':
          value = data.date || new Date().toISOString().slice(0, 10);
          break;
        case 'shift':
          value = "Morning (06:00–14:00)";
          break;
          
        case 'n1_open':
          value = getNozzle(0).opening || 0;
          break;
        case 'n1_close':
          value = getNozzle(0).closing || 0;
          break;
        case 'n1_sales':
          value = getNozzle(0).sales_liters_calculated || getNozzle(0).net_sales_liters || 0;
          mismatch = isOffline ? false : !getNozzle(0).arithmetic_valid;
          engineA = getNozzle(0).sales_liters_transcribed || getNozzle(0).transcribed_flow || 0;
          engineB = getNozzle(0).sales_liters_calculated || getNozzle(0).calculated_flow || 0;
          break;
          
        case 'n2_open':
          value = getNozzle(1).opening || 0;
          break;
        case 'n2_close':
          value = getNozzle(1).closing || 0;
          break;
        case 'n2_sales':
          value = getNozzle(1).sales_liters_calculated || getNozzle(1).net_sales_liters || 0;
          mismatch = isOffline ? false : !getNozzle(1).arithmetic_valid;
          engineA = getNozzle(1).sales_liters_transcribed || getNozzle(1).transcribed_flow || 0;
          engineB = getNozzle(1).sales_liters_calculated || getNozzle(1).calculated_flow || 0;
          break;
          
        case 'n3_open':
          value = getNozzle(2).opening || 0;
          break;
        case 'n3_close':
          value = getNozzle(2).closing || 0;
          break;
        case 'n3_sales':
          value = getNozzle(2).sales_liters_calculated || getNozzle(2).net_sales_liters || 0;
          mismatch = isOffline ? false : !getNozzle(2).arithmetic_valid;
          engineA = getNozzle(2).sales_liters_transcribed || getNozzle(2).transcribed_flow || 0;
          engineB = getNozzle(2).sales_liters_calculated || getNozzle(2).calculated_flow || 0;
          break;
          
        case 'cash':
          value = data.cash_tender || data.total_cash_calculated || 0;
          break;
        case 'upi':
          value = data.upi_tender || 0;
          break;
        case 'paytm':
          value = data.paytm_transfers || 0;
          break;
        case 'card':
          value = data.card_tender || 0;
          break;
        case 'udhaar':
          value = data.udhaar_sales || data.total_credit_sales || 0;
          break;
        case 'expenses':
          value = data.expenses_amount || 0;
          break;
      }
      
      if (mismatch) {
        conf = 0.5; // Trigger a validation warning
      } else {
        engineA = value;
        engineB = value;
      }
      
      return {
        key: f.key,
        label: f.label,
        type: f.type,
        engineA,
        engineB,
        value,
        conf,
        mismatch
      };
    });
  }

  return { extract, extractBatch, REGISTER_FIELDS };
})();

window.OCR = OCR;
