"""
Programmatic Translation Override Module.
Allows local deterministic rules in deterministic_rules.json to map messy
handwritten variations or specific text directly to clean accounting heads.
"""

import os
import re
import json
from typing import Dict, Any, List
from logger import logger

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(BACKEND_DIR, "deterministic_rules.json")

def load_override_rules(rules_path: str = RULES_PATH) -> Dict[str, Any]:
    """
    Loads translation override rules from the JSON configuration file.
    Returns a dictionary of replacements and regex patterns.
    """
    if not os.path.exists(rules_path):
        logger.warning(f"Override rules file not found at {rules_path}. Returning empty rules.")
        return {"replacements": {}, "regex_patterns": {}}
        
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = json.load(f)
            return {
                "replacements": rules.get("replacements") or {},
                "regex_patterns": rules.get("regex_patterns") or {}
            }
    except Exception as e:
        logger.error(f"Failed to load translation override rules: {str(e)}")
        return {"replacements": {}, "regex_patterns": {}}

def apply_hard_overrides(structured_json_payload: Any, rules_path: str = RULES_PATH) -> Any:
    """
    Post-Extraction Interceptor Function:
    Runs directly on the JSON output payload. String normalizes all party names, 
    remarks, vehicle numbers, and item names using deterministic override rules.
    """
    rules = load_override_rules(rules_path)
    replacements = rules.get("replacements") or {}
    regex_patterns = rules.get("regex_patterns") or {}
    
    if not replacements and not regex_patterns:
        return structured_json_payload
        
    def normalize_string(val_str: str) -> str:
        if not val_str:
            return val_str
            
        # 1. Apply replacements (overwrite whole string if messy variation matches/contains key)
        for messy_key, clean_val in replacements.items():
            if messy_key.strip().lower() in val_str.strip().lower():
                logger.info(f"Applying override replacement: '{val_str}' -> '{clean_val}'")
                return clean_val
                
        # 2. Apply regex patterns
        modified = val_str
        for pattern, replacement in regex_patterns.items():
            try:
                # Compile regex and check for match
                compiled = re.compile(pattern, re.IGNORECASE)
                if compiled.search(modified):
                    new_val = compiled.sub(replacement, modified)
                    logger.info(f"Applying override regex: '{modified}' -> '{new_val}'")
                    modified = new_val
            except Exception as regex_err:
                logger.warning(f"Failed to execute override regex '{pattern}': {str(regex_err)}")
                
        return modified

    def traverse_and_override(node: Any) -> Any:
        if isinstance(node, dict):
            new_dict = {}
            for k, v in node.items():
                # If key corresponds to names, remarks, or items and value is string, normalize
                if k in ("party_name", "remarks", "vehicle_no", "item_name") and isinstance(v, str):
                    new_dict[k] = normalize_string(v)
                elif isinstance(v, (dict, list)):
                    new_dict[k] = traverse_and_override(v)
                else:
                    new_dict[k] = v
            return new_dict
        elif isinstance(node, list):
            return [traverse_and_override(item) for item in node]
        else:
            return node

    logger.info("Executing translation override module interceptor...")
    return traverse_and_override(structured_json_payload)
