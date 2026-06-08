import os
import json
import re
import logging
import requests
import base64
from openai import OpenAI
from google import genai
from google.genai import types
import anthropic

# Setup unified logging
from logger import logger

# API cost accounting
from cost_tracker import (
    log_api_transaction,
    check_budget,
    BudgetExceededError,
)

# =====================================================================
# Prompt 3: Unified Entry Point & Advanced AI Pipeline
# =====================================================================

def _encode_image_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode('utf-8')

def extract_vision_layer(image_path: str, engine_choice: str) -> str:
    """
    Stage 1: Vision Extraction Layer.
    Extracts raw text from register sheet image using chosen vision engine.
    """
    logger.info(f"Vision Layer: Choice is '{engine_choice}', path: '{image_path}'")
    
    with open(image_path, "rb") as f:
        image_bytes = f.read()
        
    if engine_choice == "gemini":
        return run_gemini_vision_extraction(image_bytes)
        
    elif engine_choice == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("Missing OPENAI_API_KEY")
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        client = OpenAI(api_key=api_key)
        
        base64_image = _encode_image_to_base64(image_bytes)
        
        from learning_cache import HandwritingMemory
        injected_context = HandwritingMemory.get_injected_context_prompt()
        glossary = (
            "DOMAIN GLOSSARY:\n"
            "- 'Udhaar/Baki' / 'उधार' / 'बाकी': Represents credit sales (receivables) to customer accounts.\n"
            "- 'व्हील न' / 'Wheel No' / 'गाड़ी नंबर': Represents vehicle wheel number / license plate.\n"
            "- 'HSD' / 'एचएसडी': High Speed Diesel (Diesel fuel).\n"
            "- 'MS' / 'एमएस': Motor Spirit (Standard Petrol fuel).\n"
            "- 'Testing' / 'टेस्टिंग': Daily 5-liter calibration tests performed on the nozzle.\n"
            "- 'Lube/Lubricant/Servo/Coolant' / 'ल्युब/तेल': Represents ancillary inventory items and lubricant products.\n"
            "- 'POS / Swipe / Card Machine' / 'स्वाइप / मशीन': Represents separate card swipe machine terminals and their daily swipe gross totals.\n"
        )
        prompt = (
            f"{glossary}\n"
            f"{injected_context}\n"
            "INSTRUCTIONS:\n"
            "1. Extract all raw text lines, handwriting groupings, nozzle boxes, and the credit sales list from this image.\n"
            "2. Transcribe columns, headers, opening/closing meters, and rates precisely.\n"
            "3. Locate credit list entries (names, vehicle numbers, amounts) and side annotations literal and intact.\n"
            "4. Isolate non-fuel ancillary items and lubricants written outside the fuel grids (often containing item quantities, multipliers, and prices, e.g. '2 petic oil @ 350 = 700' or 'Lube 5L x 1').\n"
            "5. Locate swipe POS machine totals (often written near digital drops or expense lists, e.g., 'HDFC Swipe - 12,400' or 'Card sale - SBI machine 8,500').\n"
            "6. Using the domain glossary, translate or label terms accurately (e.g. identify wheel numbers as vehicle numbers).\n"
            "7. Output ONLY the raw literal transcription without adding personal opinions or summary reports."
        )
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]
        )
        return response.choices[0].message.content
        
    elif engine_choice == "local":
        base_url = os.getenv("LOCAL_AI_BASE_URL", "http://localhost:11434/v1")
        model_name = os.getenv("LOCAL_VISION_MODEL", "llama3.2-vision:latest")
        client = OpenAI(base_url=base_url, api_key="ollama")
        
        base64_image = _encode_image_to_base64(image_bytes)
        
        from learning_cache import HandwritingMemory
        injected_context = HandwritingMemory.get_injected_context_prompt()
        glossary = (
            "DOMAIN GLOSSARY:\n"
            "- 'Udhaar/Baki' / 'उधार' / 'बाकी': Represents credit sales (receivables) to customer accounts.\n"
            "- 'व्हील न' / 'Wheel No' / 'गाड़ी नंबर': Represents vehicle wheel number / license plate.\n"
            "- 'HSD' / 'एचएसडी': High Speed Diesel (Diesel fuel).\n"
            "- 'MS' / 'एमएस': Motor Spirit (Standard Petrol fuel).\n"
            "- 'Testing' / 'टेस्टिंग': Daily 5-liter calibration tests performed on the nozzle.\n"
            "- 'Lube/Lubricant/Servo/Coolant' / 'ल्युब/तेल': Represents ancillary inventory items and lubricant products.\n"
            "- 'POS / Swipe / Card Machine' / 'स्वाइप / मशीन': Represents separate card swipe machine terminals and their daily swipe gross totals.\n"
        )
        prompt = (
            f"{glossary}\n"
            f"{injected_context}\n"
            "INSTRUCTIONS:\n"
            "1. Extract all raw text lines, handwriting groupings, nozzle boxes, and the credit sales list from this image.\n"
            "2. Transcribe columns, headers, opening/closing meters, and rates precisely.\n"
            "3. Locate credit list entries (names, vehicle numbers, amounts) and side annotations literal and intact.\n"
            "4. Isolate non-fuel ancillary items and lubricants written outside the fuel grids (often containing item quantities, multipliers, and prices, e.g. '2 petic oil @ 350 = 700' or 'Lube 5L x 1').\n"
            "5. Locate swipe POS machine totals (often written near digital drops or expense lists, e.g., 'HDFC Swipe - 12,400' or 'Card sale - SBI machine 8,500').\n"
            "6. Using the domain glossary, translate or label terms accurately (e.g. identify wheel numbers as vehicle numbers).\n"
            "7. Output ONLY the raw literal transcription without adding personal opinions or summary reports."
        )
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ]
        )
        return response.choices[0].message.content
    else:
        raise ValueError(f"Unsupported vision engine choice: {engine_choice}")

def execute_accounting_logic(raw_text: str, engine_choice: str) -> dict:
    """
    Stage 2: Accounting Logic Layer.
    Audits transcription text, cross-checks calculations, and returns structured JSON.
    """
    logger.info(f"Logic Layer: Choice is '{engine_choice}'")
    
    rates_context = ""
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", raw_text)
    if not date_match:
        date_match_alt = re.search(r"\b(\d{2}-\d{2}-\d{4})\b", raw_text)
        if date_match_alt:
            parts = date_match_alt.group(1).split("-")
            date_str = f"{parts[2]}-{parts[1]}-{parts[0]}"
        else:
            date_str = None
    else:
        date_str = date_match.group(1)
        
    if date_str:
        try:
            from price_registry import get_rates_for_date
            from premium_products import resolve_variant_rate
            rates = get_rates_for_date(date_str)
            if rates:
                r_hsd = rates['hsd_rate']
                r_ms = rates['ms_rate']
                p_hsd = resolve_variant_rate(rates, 'PREMIUM_HSD')
                p_ms = resolve_variant_rate(rates, 'PREMIUM_MS')
                rates_context = (
                    f"Verified reference rates for this specific day:\n"
                    f"- Regular Diesel (REGULAR_HSD): {r_hsd:.2f}\n"
                    f"- Premium Diesel (PREMIUM_HSD, e.g. XTRAGREEN): {p_hsd:.2f}\n"
                    f"- Regular Petrol (REGULAR_MS): {r_ms:.2f}\n"
                    f"- Premium Petrol (PREMIUM_MS, e.g. XP95, Speed): {p_ms:.2f}"
                )
        except Exception as rates_err:
            logger.warning(f"Error querying price registry inside logic layer: {str(rates_err)}")

    if engine_choice == "claude":
        return run_claude_accounting_guardrails(raw_text, rates_context)
        
    elif engine_choice == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("Missing OPENAI_API_KEY")
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        client = OpenAI(api_key=api_key)
        
        system_prompt = (
            "You are a rigorous pump accountant auditing daily registers.\n"
            "Enforce these strict accounting rules on the transcribed register text:\n\n"
        )
        if rates_context:
            system_prompt += f"{rates_context}. Use these exact figures to calculate totalizer sales if the handwriting on the sheet is unreadable or faded or differs.\n\n"
            
        system_prompt += (
            "1. TOTALIZER CHECK: Calculate calculated_flow = (Closing Value - Opening Value) for every nozzle sequence. Identify explicit brand names, numbers, or shorthand labels indicating high-octane fuel nozzles written in the register margins or nozzle names (e.g. 'XP 95', 'XP95', 'Speed', 'Xtragreen', '95 Octane line', 'Premium Petrol'). Set the nozzle's 'fuel_type' to one of the four canonical variant SKU tags: 'REGULAR_HSD', 'PREMIUM_HSD', 'REGULAR_MS', or 'PREMIUM_MS'. Make sure to map brand names like 'XP95', 'Speed', 'Octane 95' to 'PREMIUM_MS', and 'XTRAGREEN', 'Xtramile', 'Premium Diesel' to 'PREMIUM_HSD'. Default standard diesel to 'REGULAR_HSD' and standard petrol to 'REGULAR_MS'. Identify handwritten notation markers indicating a meter change, replacement, or mechanical rollover line (e.g., 'Meter Changed', 'नया मीटर चालू', 'Rollover at 99999', 'Reset', 'New Meter'). If such notation markers are found, note them in the nozzle's 'remarks' or 'notes' field, and set 'meter_replaced' to true. Cross-verify if calculated_flow matches the manual transcribed sales liters. If they differ, raise a math warning.\n"
            "2. TESTING DEDUCTIONS: If daily calibration 'Testing' liters are present, watch for explicit testing marks inside the totalizer sections (such as circled number lines or entries labeled 'Testing', 'Test', or 'RTS'). Subtract these testing volumes (usually multiples of 5) from the nozzle's gross flow to get net sales: net_sales = calculated_flow - testing_liters.\n"
            "3. CROSS-VERIFY LITERS: Sum net sales for all MS nozzles (regular and premium) and all HSD nozzles (regular and premium). Cross-verify if they match the manual summaries recorded in the register layout.\n"
            "4. CREDIT LEDGER EXTRACTION: Isolate individual credit names ('Udhaar'/'Baki') and vehicle wheel numbers. Extract the exact liters drawn (liters_drawn), transaction amounts, and remarks.\n"
            "5. DSM SHIFT EXTRACTION: Identify DSM names, shift type, assigned nozzles, cash handed over, digital slips value, and shortages/excesses.\n"
            "6. NON-FUEL ASSET & LUBRICANT EXTRACTION: Identify non-fuel and lubricant sales and revenue.\n"
            "7. CARD SWIPE MACHINE EXTRACTION: Identify card POS swipe machine gross provider and totals.\n"
            "8. CREDIT BALANCE REALIZATION: Extract customer payments clear lines.\n"
            "9. STAFF CASH ADVANCES: Extract staff advances or fuel draws.\n"
            "10. Output JSON schema compliant format only."
        )
        
        json_schema_example = """
{
  "date": "YYYY-MM-DD",
  "nozzles": [
    {
      "nozzle_name": "Nozzle 1 (HSD)",
      "fuel_type": "REGULAR_HSD",
      "opening": 12345.6,
      "closing": 12567.8,
      "calculated_flow": 222.2,
      "transcribed_flow": 222.2,
      "testing_liters": 5.0,
      "net_sales_liters": 217.2,
      "rate": 90.5,
      "amount_calculated": 19656.6,
      "amount_transcribed": 19656.6,
      "is_valid": true,
      "math_warning": null,
      "meter_replaced": false,
      "replacement_offset_liters": 0.0
    }
  ],
  "credit_sales": [
    {
      "party_name": "Rahul Transport",
      "vehicle_no": "HR-38-F-1234",
      "liters_drawn": 50.0,
      "amount": 8500.0,
      "remarks": "HSD credit sale"
    }
  ],
  "cash_expenses": [
    {
      "party_name": "Office Expense",
      "amount": 150.0,
      "remarks": "Tea & Cleaning"
    }
  ],
  "dsm_shifts": [
    {
      "dsm_name": "Ramesh",
      "shift_type": "Day",
      "assigned_nozzles": ["MS-1", "MS-2"],
      "cash_handed_over": 45000.0,
      "digital_slips_value": 0.0,
      "calculated_shortage_or_excess": -120.0
    }
  ],
  "lube_sales": [
    {
      "item_name": "Petic Oil",
      "quantity_sold": 2.0,
      "unit_price": 350.0,
      "total_item_revenue": 700.0
    }
  ],
  "card_settlements": [
    {
      "machine_provider": "HDFC POS",
      "gross_swipes_copied": 12400.0
    }
  ],
  "credit_realizations": [
    {
      "party_name": "Gopalram Ji Dhaba",
      "amount_received": 15000.0,
      "payment_mode": "CASH",
      "bank_utr_or_remarks": "balance clear"
    }
  ],
  "staff_advances": [
    {
      "employee_name": "Ramesh",
      "amount_drawn": 500.0,
      "type": "CASH_ADVANCE",
      "remarks": "DSM advance 500"
    }
  ],
  "total_calculated_liters_hsd": 217.2,
  "total_calculated_liters_ms": 0.0,
  "total_cash_calculated": 19656.6,
  "total_credit_sales": 8500.0,
  "total_testing_deductions": 5.0,
  "validation_status": "balanced",
  "mathematical_warnings": [
    "Warning: Nozzle 1 math matches manual summaries"
  ]
}
"""
        prompt = f"Parse and return valid JSON matching this schema:\n{raw_text}\n\nSCHEMA:\n{json_schema_example}"
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
        
    elif engine_choice == "local":
        base_url = os.getenv("LOCAL_AI_BASE_URL", "http://localhost:11434/v1")
        model_name = os.getenv("LOCAL_LOGIC_MODEL", "qwen2.5:7b")
        client = OpenAI(base_url=base_url, api_key="ollama")
        
        system_prompt = (
            "You are a rigorous pump accountant auditing daily registers. "
            "Output JSON format matching standard pump accounts schema."
        )
        prompt = f"Extract accounting facts and output clean JSON schema format from text:\n{raw_text}"
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        )
        content = response.choices[0].message.content
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        return json.loads(content)
    else:
        raise ValueError(f"Unsupported logic engine choice: {engine_choice}")

def analyze_register_sheet(processed_image_path: str, vision_engine: str = "gemini", logic_engine: str = "claude") -> dict:
    """
    Unified entry point that coordinates the two-step AI engine:
    Step 1: Vision extraction using the specified engine.
    Step 2: Accounting logic analysis using the specified engine.
    """
    logger.info(f"Analyzing register sheet at path: {processed_image_path} (vision: {vision_engine}, logic: {logic_engine})...")
    try:
        if not os.path.exists(processed_image_path):
            raise FileNotFoundError(f"Processed image at '{processed_image_path}' not found.")

        check_budget()

        # Step 1: Vision extraction
        raw_text_extraction = extract_vision_layer(processed_image_path, vision_engine)
        
        # Step 2: Accounting logic checks
        final_accounting_json = execute_accounting_logic(raw_text_extraction, logic_engine)
        
        # Apply overrides
        try:
            from rule_override import apply_hard_overrides
            final_accounting_json = apply_hard_overrides(final_accounting_json)
        except Exception as override_err:
            logger.warning(f"Failed to apply overrides inside analyze_register_sheet: {str(override_err)}")
        
        final_accounting_json["raw_transcription_text"] = raw_text_extraction
        
        # Apply customer special contract pricing and discounts (Prompt 88)
        try:
            from discount_manager import apply_contract_discounts
            credit_sales = final_accounting_json.get("credit_sales", [])
            date_str = final_accounting_json.get("date")
            if credit_sales and date_str and date_str != "N/A":
                backend_dir = os.path.dirname(os.path.abspath(__file__))
                db_path = os.path.join(backend_dir, "ledger.db")
                final_accounting_json["credit_sales"] = apply_contract_discounts(credit_sales, date_str, db_path)
                # Recalculate total_credit_sales sum
                credit_sum = sum(float(c.get("amount") or 0.0) for c in final_accounting_json["credit_sales"])
                final_accounting_json["total_credit_sales"] = round(credit_sum, 2)
        except Exception as discount_err:
            logger.warning(f"Failed to apply customer contract discounts in AI pipeline: {str(discount_err)}")

        # Run credit limit enforcement and allocation safety check (Prompt 99)
        try:
            from credit_guard import verify_transaction_credit_safety
            credit_sales = final_accounting_json.get("credit_sales", [])
            backend_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(backend_dir, "ledger.db")
            for sale in credit_sales:
                party = sale.get("party_name")
                amount = float(sale.get("amount") or 0.0)
                if party:
                    safety_res = verify_transaction_credit_safety(party, amount, db_path=db_path)
                    if safety_res.get("credit_status") == "THRESHOLD_BREACH_WARNING":
                        sale["credit_status"] = "THRESHOLD_BREACH_WARNING"
        except Exception as credit_guard_err:
            logger.warning(f"Failed to apply credit limit guard check in AI pipeline: {str(credit_guard_err)}")

        logger.info("Register sheet analysis completed successfully!")
        return final_accounting_json
        
    except BudgetExceededError as bge:
        logger.warning(str(bge))
        logger.warning("Switching to offline fallback.")
        offline_json = _build_offline_template()
        offline_json["mathematical_warnings"] = [str(bge)]
        return offline_json
    except Exception as e:
        logger.warning(f"AI Pipeline Failed: {str(e)}")
        logger.warning("Switching to offline backup.")
        return _build_offline_template()

def _build_offline_template() -> dict:
    """Returns a zero-initialised offline ledger template."""
    from datetime import datetime
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "nozzles": [
            {
                "nozzle_name": "MS-1 (Petrol)",
                "fuel_type": "REGULAR_MS",
                "opening": 0.0, "closing": 0.0,
                "calculated_flow": 0.0, "transcribed_flow": 0.0,
                "testing_liters": 0.0, "net_sales_liters": 0.0,
                "rate": 106.31, "amount_calculated": 0.0,
                "amount_transcribed": 0.0, "is_valid": True, "math_warning": None,
            },
            {
                "nozzle_name": "MS-2 (Petrol)",
                "fuel_type": "REGULAR_MS",
                "opening": 0.0, "closing": 0.0,
                "calculated_flow": 0.0, "transcribed_flow": 0.0,
                "testing_liters": 0.0, "net_sales_liters": 0.0,
                "rate": 106.31, "amount_calculated": 0.0,
                "amount_transcribed": 0.0, "is_valid": True, "math_warning": None,
            },
            {
                "nozzle_name": "HSD-1 (Diesel)",
                "fuel_type": "REGULAR_HSD",
                "opening": 0.0, "closing": 0.0,
                "calculated_flow": 0.0, "transcribed_flow": 0.0,
                "testing_liters": 0.0, "net_sales_liters": 0.0,
                "rate": 94.27, "amount_calculated": 0.0,
                "amount_transcribed": 0.0, "is_valid": True, "math_warning": None,
            },
        ],
        "credit_sales":  [{"party_name": "", "vehicle_no": "", "amount": 0.0, "remarks": ""}],
        "cash_expenses": [{"party_name": "", "amount": 0.0, "remarks": ""}],
        "total_calculated_liters_hsd": 0.0,
        "total_calculated_liters_ms":  0.0,
        "total_cash_calculated":        0.0,
        "total_credit_sales":           0.0,
        "total_testing_deductions":     0.0,
        "validation_status":  "offline_review",
        "mathematical_warnings": ["Cloud APIs offline fallback activated. Please fill in details manually."],
        "offline_mode": True,
        "dsm_shifts": [],
        "card_settlements": [],
        "staff_advances": []
    }


def run_gemini_vision_extraction(image_bytes: bytes) -> str:
    """
    Invokes Gemini 1.5 Flash with a strict prompt and domain glossary to perform
    raw text extraction of registers, table cells, and handwriting comments.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable is not set.")
        raise ValueError("Missing GEMINI_API_KEY. Please set it in your environment or .env file.")
        
    client = genai.Client(api_key=api_key)
    
    # Domain Glossary injection
    glossary = (
        "DOMAIN GLOSSARY:\n"
        "- 'Udhaar/Baki' / 'उधार' / 'बाकी': Represents credit sales (receivables) to customer accounts.\n"
        "- 'व्हील न' / 'Wheel No' / 'गाड़ी नंबर': Represents vehicle wheel number / license plate.\n"
        "- 'HSD' / 'एचएसडी': High Speed Diesel (Diesel fuel).\n"
        "- 'MS' / 'एमएस': Motor Spirit (Standard Petrol fuel).\n"
        "- 'Testing' / 'टेस्टिंग': Daily 5-liter calibration tests performed on the nozzle. "
        "This volume is dispensed but subtracted from net sales.\n"
        "- 'Lube/Lubricant/Servo/Coolant' / 'ल्युब/तेल': Represents ancillary inventory items and lubricant products.\n"
        "- 'POS / Swipe / Card Machine' / 'स्वाइप / मशीन': Represents separate card swipe machine terminals and their daily swipe gross totals.\n"
    )
    from learning_cache import HandwritingMemory
    injected_context = HandwritingMemory.get_injected_context_prompt()
    
    prompt = (
        f"{glossary}\n"
        f"{injected_context}\n"
        "INSTRUCTIONS:\n"
        "1. Extract all raw text lines, handwriting groupings, nozzle boxes, and the credit sales list from this image.\n"
        "2. Transcribe columns, headers, opening/closing meters, and rates precisely.\n"
        "3. Locate credit list entries (names, vehicle numbers, amounts) and side annotations literal and intact.\n"
        "4. Isolate non-fuel ancillary items and lubricants written outside the fuel grids (often containing item quantities, multipliers, and prices, e.g. '2 petic oil @ 350 = 700' or 'Lube 5L x 1').\n"
        "5. Locate swipe POS machine totals (often written near digital drops or expense lists, e.g., 'HDFC Swipe - 12,400' or 'Card sale - SBI machine 8,500').\n"
        "6. Using the domain glossary, translate or label terms accurately (e.g. identify wheel numbers as vehicle numbers).\n"
        "7. Output ONLY the raw literal transcription without adding personal opinions or summary reports."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type='image/png',
                ),
                prompt
            ]
        )
        # ── Cost tracking ────────────────────────────────────────────────
        log_api_transaction("gemini", response)
        return response.text
    except Exception as e:
        logger.error(f"Gemini Vision Extraction failed: {str(e)}")
        raise RuntimeError(f"Gemini Extraction Failed: {str(e)}")

def run_claude_accounting_guardrails(raw_transcript: str, rates_context: str = "") -> dict:
    """
    Passes Gemini's transcription to Claude 3.5 Sonnet to enforce strict pump
    accounting guardrails, execute totalizer checks, and parse structured JSON.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable is not set.")
        raise ValueError("Missing ANTHROPIC_API_KEY. Please set it in your environment or .env file.")
        
    client = anthropic.Anthropic(api_key=api_key)
    
    system_prompt = (
        "You are a rigorous pump accountant auditing daily registers.\n"
        "Enforce these strict accounting rules on the transcribed register text:\n\n"
    )
    if rates_context:
        system_prompt += f"{rates_context}. Use these exact figures to calculate totalizer sales if the handwriting on the sheet is unreadable or faded or differs.\n\n"
        
    system_prompt += (
        "1. TOTALIZER CHECK: Calculate calculated_flow = (Closing Value - Opening Value) for every nozzle sequence. Identify explicit brand names, numbers, or shorthand labels indicating high-octane fuel nozzles written in the register margins or nozzle names (e.g. 'XP 95', 'XP95', 'Speed', 'Xtragreen', '95 Octane line', 'Premium Petrol'). Set the nozzle's 'fuel_type' to one of the four canonical variant SKU tags: 'REGULAR_HSD', 'PREMIUM_HSD', 'REGULAR_MS', or 'PREMIUM_MS'. Make sure to map brand names like 'XP95', 'Speed', 'Octane 95' to 'PREMIUM_MS', and 'XTRAGREEN', 'Xtramile', 'Premium Diesel' to 'PREMIUM_HSD'. Default standard diesel to 'REGULAR_HSD' and standard petrol to 'REGULAR_MS'. Identify handwritten notation markers indicating a meter change, replacement, or mechanical rollover line (e.g., 'Meter Changed', 'नया मीटर चालू', 'Rollover at 99999', 'Reset', 'New Meter'). If such notation markers are found, note them in the nozzle's 'remarks' or 'notes' field, and set 'meter_replaced' to true. Cross-verify if calculated_flow matches the manual transcribed sales liters. If they differ, raise a math warning.\n"
        "2. TESTING DEDUCTIONS: If daily calibration 'Testing' liters are present, watch for explicit testing marks inside the totalizer sections (such as circled number lines or entries labeled 'Testing', 'Test', or 'RTS'). Subtract these testing volumes (usually multiples of 5) from the nozzle's gross flow to get net sales: net_sales = calculated_flow - testing_liters.\n"
        "3. CROSS-VERIFY LITERS: Sum net sales for all MS nozzles (regular and premium) and all HSD nozzles (regular and premium). Cross-verify if they match the manual summaries recorded in the register layout. If they differ, generate an accounting alert.\n"
        "4. CREDIT LEDGER EXTRACTION: Isolate individual credit names ('Udhaar'/'Baki'). Extract vehicle wheel numbers next to each party name, "
        "along with the exact liters drawn (liters_drawn), transaction amounts, and remarks.\n"
        "5. DSM SHIFT EXTRACTION: Identify names of salesmen/DSM written near nozzle figures, column headers, or margin accounts, alongside notes indicating individual cash/digital handovers and shortages/excesses (e.g., 'Ramesh DSM - ₹45,000 deposits, short ₹120', 'Ramesh: MS-1', 'Suresh: HSD-1'). "
        "For each DSM, extract: 'dsm_name' (string), 'shift_type' (Day or Night, default to Day), 'assigned_nozzles' (array of strings, e.g. ['MS-1', 'MS-2']), 'cash_handed_over' (float), 'digital_slips_value' (float, default to 0.0), and 'calculated_shortage_or_excess' (float, default to 0.0).\n"
        "6. NON-FUEL ASSET & LUBRICANT EXTRACTION: Identify non-fuel ancillary and lubricant items mentioned outside the primary fuel totalizer grids (e.g., '2 petic oil @ 350 = 700' or 'Lube 5L x 1'). For each item, extract: 'item_name' (string, e.g., 'Petic Oil', 'Servo 4T'), 'quantity_sold' (float), 'unit_price' (float), and 'total_item_revenue' (float, default to quantity_sold * unit_price).\n"
        "7. CARD SWIPE MACHINE EXTRACTION: Identify card swipe machine transaction totals listed in expense/credit blocks or next to daily summaries (e.g. 'HDFC Swipe - 12,400' or 'Card sale - SBI machine 8,500'). For each machine record, extract: 'machine_provider' (string, e.g. 'HDFC POS', 'SBI Touch') and 'gross_swipes_copied' (float, gross swipe value).\n"
        "8. CREDIT BALANCE REALIZATION EXTRACTION: Identify explicit balance realization payments received from customers written down in the registers (e.g. 'Gopalram ji se aaye - ₹15,000' or 'Jagveer ji balance clear cash - ₹8,000'). For each realization, extract: 'party_name' (string, e.g. 'Gopalram Ji Dhaba'), 'amount_received' (float), 'payment_mode' (string: 'CASH', 'BANK_TRANSFER', or 'UPI' based on context, default to 'CASH'), and 'bank_utr_or_remarks' (string, e.g. 'balance clear', 'SBI transfer').\n"
        "9. STAFF CASH ADVANCES & INTERNAL KHARCH EXTRACTION: Identify personal names (DSM/salesmen/staff) matched with small cash values or text lines indicating immediate employee withdrawals or fuel draws outside your regular commercial credit accounts (e.g. 'Ramesh DSM advance 500' or 'Suresh kharch - 200' or 'Mahesh DSM diesel 15L'). For each staff advance/draw, extract: 'employee_name' (string), 'amount_drawn' (float), 'type' (string: 'CASH_ADVANCE' or 'FUEL_DRAWN' based on context, default to 'CASH_ADVANCE'), and 'remarks' (string, e.g. 'DSM advance', 'diesel 15L').\n"
        "10. Output the result in the exact JSON format below. Do not add any conversational text or markdown blocks outside the JSON."
    )
    
    json_schema_example = """
{
  "date": "YYYY-MM-DD",
  "nozzles": [
    {
      "nozzle_name": "Nozzle 1 (HSD)",
      "fuel_type": "REGULAR_HSD",
      "opening": 12345.6,
      "closing": 12567.8,
      "calculated_flow": 222.2,
      "transcribed_flow": 222.2,
      "testing_liters": 5.0,
      "net_sales_liters": 217.2,
      "rate": 90.5,
      "amount_calculated": 19656.6,
      "amount_transcribed": 19656.6,
      "is_valid": true,
      "math_warning": null,
      "meter_replaced": false,
      "replacement_offset_liters": 0.0
    }
  ],
  "credit_sales": [
    {
      "party_name": "Rahul Transport",
      "vehicle_no": "HR-38-F-1234",
      "liters_drawn": 50.0,
      "amount": 8500.0,
      "remarks": "HSD credit sale"
    }
  ],
  "cash_expenses": [
    {
      "party_name": "Office Expense",
      "amount": 150.0,
      "remarks": "Tea & Cleaning"
    }
  ],
  "dsm_shifts": [
    {
      "dsm_name": "Ramesh",
      "shift_type": "Day",
      "assigned_nozzles": ["MS-1", "MS-2"],
      "cash_handed_over": 45000.0,
      "digital_slips_value": 0.0,
      "calculated_shortage_or_excess": -120.0
    }
  ],
  "lube_sales": [
    {
      "item_name": "Petic Oil",
      "quantity_sold": 2.0,
      "unit_price": 350.0,
      "total_item_revenue": 700.0
    }
  ],
  "card_settlements": [
    {
      "machine_provider": "HDFC POS",
      "gross_swipes_copied": 12400.0
    }
  ],
  "credit_realizations": [
    {
      "party_name": "Gopalram Ji Dhaba",
      "amount_received": 15000.0,
      "payment_mode": "CASH",
      "bank_utr_or_remarks": "balance clear"
    }
  ],
  "staff_advances": [
    {
      "employee_name": "Ramesh",
      "amount_drawn": 500.0,
      "type": "CASH_ADVANCE",
      "remarks": "DSM advance 500"
    }
  ],
  "total_calculated_liters_hsd": 217.2,
  "total_calculated_liters_ms": 0.0,
  "total_cash_calculated": 19656.6,
  "total_credit_sales": 8500.0,
  "total_testing_deductions": 5.0,
  "validation_status": "balanced",
  "mathematical_warnings": [
    "Warning: Nozzle 1 math matches manual summaries"
  ]
}
"""
    
    prompt = (
        f"Here is the raw register text:\n\n{raw_transcript}\n\n"
        f"Parse and return valid JSON matching this schema:\n{json_schema_example}"
    )
    
    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4000,
            temperature=0.0,
            system=system_prompt,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        # ── Cost tracking ────────────────────────────────────────────────
        log_api_transaction("anthropic", response)

        raw_output = response.content[0].text
        logger.info("Claude validation completed.")
        
        # Extract JSON block
        json_match = re.search(r"({.*})", raw_output, re.DOTALL)
        if json_match:
            parsed_json = json.loads(json_match.group(1))
        else:
            parsed_json = json.loads(raw_output)
            
        # Re-verify calculations in python as secondary audit layer
        logger.info("Executing secondary python mathematical audits...")
        total_hsd = 0.0
        total_ms = 0.0
        total_cash = 0.0
        warnings = parsed_json.get("mathematical_warnings", [])
        
        # Get date from parsed_json to load rates
        date_str = parsed_json.get("date")
        rates = None
        if date_str:
            try:
                from price_registry import get_rates_for_date
                rates = get_rates_for_date(date_str)
            except Exception as rates_err:
                logger.warning(f"Error querying price registry in secondary audit: {str(rates_err)}")

        for nozzle in parsed_json.get("nozzles", []):
            opening = float(nozzle.get("opening") or 0.0)
            closing = float(nozzle.get("closing") or 0.0)
            testing = float(nozzle.get("testing_liters") or 0.0)
            trans_flow = float(nozzle.get("transcribed_flow") or 0.0)
            
            # Normalize and resolve fuel_type
            raw_fuel_type = nozzle.get("fuel_type") or ""
            from premium_products import map_nozzle_brand_to_sku, resolve_variant_rate
            
            if raw_fuel_type in ("HSD", "MS", "") or not raw_fuel_type:
                mapped_type = map_nozzle_brand_to_sku(nozzle.get("nozzle_name") or "")
                nozzle["fuel_type"] = mapped_type
            else:
                nozzle["fuel_type"] = str(raw_fuel_type).strip().upper()
                
            # Calibrate rates
            rate = None
            prev_date = None
            if rates:
                rate = resolve_variant_rate(rates, nozzle["fuel_type"])
            
            if not rate or rate == 0.0:
                try:
                    from price_gap_filler import resolve_missing_fuel_price
                    lookback_rate, lookback_date = resolve_missing_fuel_price(date_str, nozzle["fuel_type"])
                    if lookback_rate:
                        rate = lookback_rate
                        prev_date = lookback_date
                        parsed_json["price_status"] = "FETCHED_FROM_HISTORICAL_FALLBACK"
                        if "fallback_prev_date" not in parsed_json or (prev_date and prev_date > parsed_json["fallback_prev_date"]):
                            parsed_json["fallback_prev_date"] = prev_date
                except Exception as lookback_err:
                    logger.warning(f"Error calling resolve_missing_fuel_price in secondary audit: {str(lookback_err)}")
            
            if not rate:
                dummy_rates = {"hsd_rate": 94.27, "ms_rate": 106.31}
                rate = resolve_variant_rate(dummy_rates, nozzle["fuel_type"])
            
            nozzle["rate"] = rate
            
            from meter_rollover import calculate_net_nozzle_volume
            is_nz_replaced = bool(nozzle.get("meter_replaced") or False)
            nz_offset = float(nozzle.get("replacement_offset_liters") or 0.0)
            calc_flow = calculate_net_nozzle_volume(opening, closing, meter_replaced=is_nz_replaced, replacement_offset_liters=nz_offset)
            nozzle["calculated_flow"] = calc_flow
            nozzle["net_sales_liters"] = round(calc_flow - testing, 2)
            
            calc_amt = round(nozzle["net_sales_liters"] * rate, 2)
            nozzle["amount_calculated"] = calc_amt
            
            # Cross-verify checks
            if abs(calc_flow - trans_flow) > 0.01:
                nozzle["is_valid"] = False
                w_msg = f"Warning: Nozzle '{nozzle.get('nozzle_name')}' Closing ({closing}) - Opening ({opening}) = Calculated {calc_flow} L, but register lists {trans_flow} L."
                nozzle["math_warning"] = w_msg
                if w_msg not in warnings:
                    warnings.append(w_msg)
            else:
                nozzle["is_valid"] = True
                
            if nozzle.get("fuel_type") in ("REGULAR_HSD", "PREMIUM_HSD", "HSD"):
                total_hsd += nozzle["net_sales_liters"]
            elif nozzle.get("fuel_type") in ("REGULAR_MS", "PREMIUM_MS", "MS"):
                total_ms += nozzle["net_sales_liters"]
                
            total_cash += nozzle["amount_calculated"]
            
        parsed_json["total_calculated_liters_hsd"] = round(total_hsd, 2)
        parsed_json["total_calculated_liters_ms"] = round(total_ms, 2)
        parsed_json["total_cash_calculated"] = round(total_cash, 2)
        
        # Calculate sum of credit
        credit_sum = sum(float(c.get("amount") or 0.0) for c in parsed_json.get("credit_sales", []))
        parsed_json["total_credit_sales"] = round(credit_sum, 2)
        
        # Calculate testing total
        testing_sum = sum(float(n.get("testing_liters") or 0.0) for n in parsed_json.get("nozzles", []))
        parsed_json["total_testing_deductions"] = round(testing_sum, 2)
        
        parsed_json["mathematical_warnings"] = warnings
        
        if any(not n.get("is_valid") for n in parsed_json.get("nozzles", [])):
            parsed_json["validation_status"] = "math_discrepancy"
        else:
            parsed_json["validation_status"] = "balanced"

        # Expense categorization — inject accounting_head into every cash_expenses entry
        try:
            from expense_mapper import categorize_loose_expenses
            if parsed_json.get("cash_expenses"):
                parsed_json["cash_expenses"] = categorize_loose_expenses(
                    parsed_json["cash_expenses"]
                )
        except Exception as _exp_map_err:
            logger.warning(f"ExpenseMapper categorization skipped: {_exp_map_err}")
            
        return parsed_json
        
    except json.JSONDecodeError as je:
        logger.error(f"JSON Decode Error from Claude: {str(je)}")
        raise ValueError(f"Claude returned invalid JSON: {str(je)}")
    except Exception as e:
        logger.error(f"Claude validation check failed: {str(e)}")
        raise RuntimeError(f"Claude validation failed: {str(e)}")


# =====================================================================
# Backward Compatibility: Maintain previous API functions
# =====================================================================

def run_gemini_ocr(image_bytes: bytes) -> str:
    """
    Saves image bytes and transcribes raw text.
    """
    logger.info("Executing legacy Gemini OCR bytes pipeline...")
    return run_gemini_vision_extraction(image_bytes)

def run_qwen_vision_ocr(image_bytes: bytes, endpoint_url: str) -> str:
    """
    Qwen vision local fallback.
    """
    logger.info("Executing Qwen vision local server OCR...")
    import base64
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    payload = {
        "model": "qwen2.5-vision",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe register details literally."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                ]
            }
        ]
    }
    headers = {"Content-Type": "application/json"}
    response = requests.post(endpoint_url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

