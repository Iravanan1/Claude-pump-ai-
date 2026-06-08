import os
import json
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LearningCache")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BACKEND_DIR, "corrections_cache.json")

class HandwritingMemory:
    """
    Lightweight continuous learning cache that tracks manual corrections
    made by the user for handwriting transcriptions to prevent repeating mistakes.
    """
    
    @staticmethod
    def _load_cache() -> dict:
        """Loads the corrections cache from local JSON file."""
        if not os.path.exists(CACHE_PATH):
            return {"corrections": {}}
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "corrections" not in data:
                    data = {"corrections": {}}
                return data
        except Exception as e:
            logger.error(f"Failed to load corrections cache: {str(e)}")
            return {"corrections": {}}

    @staticmethod
    def _save_cache(data: dict):
        """Saves the corrections cache to local JSON file."""
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to write corrections cache: {str(e)}")

    @classmethod
    def record_correction(cls, original_raw_text: str, field_type: str, corrected_text: str):
        """
        Records a manual handwriting correction and tracks its recurrence count.
        
        Args:
            original_raw_text (str): The raw text incorrectly transcribed by the AI.
            field_type (str): The category/field type of the correction (e.g. 'party_name', 'vehicle_no', 'nozzle_reading').
            corrected_text (str): The user's manual correction.
        """
        orig = str(original_raw_text or "").strip()
        corr = str(corrected_text or "").strip()
        f_type = str(field_type or "").strip()
        
        # Don't log if original and corrected are identical or empty
        if not orig or not corr or orig == corr:
            return
            
        data = cls._load_cache()
        corrections = data["corrections"]
        
        # Unique cache key based on lowercased original text and field type
        cache_key = f"{orig.lower()}_{f_type.lower()}"
        
        if cache_key in corrections:
            entry = corrections[cache_key]
            # If the user corrects it to the same value again, increment count
            if entry.get("corrected") == corr:
                entry["count"] = entry.get("count", 0) + 1
            else:
                # If corrected to a new value, update it and reset count
                entry["corrected"] = corr
                entry["count"] = 1
        else:
            corrections[cache_key] = {
                "original": orig,
                "field_type": f_type,
                "corrected": corr,
                "count": 1
            }
            
        cls._save_cache(data)
        logger.info(f"Recorded correction: '{orig}' -> '{corr}' (Field: {f_type})")

    @classmethod
    def get_injected_context_prompt(cls) -> str:
        """
        Reads the cache file and returns a formatted system prompt injection block
        containing learned top corrected terms for the AI vision parser.
        """
        data = cls._load_cache()
        corrections = data.get("corrections", {})
        
        if not corrections:
            return ""
            
        # Sort corrections by count descending to prioritize highly repeated corrections
        sorted_entries = sorted(
            corrections.values(), 
            key=lambda x: x.get("count", 1), 
            reverse=True
        )
        
        # Limit to top 15 corrections to keep prompt context highly focused and efficient
        top_entries = sorted_entries[:15]
        
        instruction_lines = []
        for entry in top_entries:
            orig = entry.get("original")
            corr = entry.get("corrected")
            f_type = entry.get("field_type", "text")
            instruction_lines.append(
                f"- For field type '{f_type}': The handwriting string '{orig}' should be parsed/transcribed as '{corr}'."
            )
            
        injected_block = (
            "\n==================================================\n"
            "INJECTED CONTINUOUS LEARNING CONTEXT:\n"
            "Refer to these historical manual corrections of past common transcription/OCR errors to ensure 100% accuracy:\n"
            + "\n".join(instruction_lines) +
            "\n==================================================\n"
        )
        
        return injected_block
