import os
import shutil
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Archiver")

def archive_processed_file(image_path: str, status: str, record_date: str = None) -> str:
    """
    Acts like a manual safe-deposit box for register image asset management:
    1. Creates three permanent directories under workspace root /ledger_photos:
       - /raw_unprocessed
       - /successfully_imported
       - /requires_human_review
    2. When a file finishes running through the pipeline, checks its status string.
    3. If status is valid/balanced/verified, moves the image to /successfully_imported
       and prefixes the filename with the verified record date.
    4. If status indicates a discrepancy or review required, moves it to /requires_human_review.
    5. Resolves filename collisions by appending a sequential suffix to prevent overwriting.
    
    Args:
        image_path (str): Absolute or relative filepath to the original register photo.
        status (str): Audited pipeline status (e.g., 'verified', 'balanced', 'needs_review', 'math_discrepancy').
        record_date (str, optional): Verified accounting record date. Defaults to today's date.
        
    Returns:
        str: The final destination filepath after the move.
    """
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    WORKSPACE_DIR = os.path.dirname(backend_dir)
    
    # Resolve permanent directory paths
    LEDGER_PHOTOS = os.path.join(WORKSPACE_DIR, "ledger_photos")
    RAW_UNPROCESSED = os.path.join(LEDGER_PHOTOS, "raw_unprocessed")
    SUCCESSFULLY_IMPORTED = os.path.join(LEDGER_PHOTOS, "successfully_imported")
    REQUIRES_REVIEW = os.path.join(LEDGER_PHOTOS, "requires_human_review")
    
    # Create permanent folders if missing
    os.makedirs(RAW_UNPROCESSED, exist_ok=True)
    os.makedirs(SUCCESSFULLY_IMPORTED, exist_ok=True)
    os.makedirs(REQUIRES_REVIEW, exist_ok=True)
    
    # Verify source image file exists
    if not os.path.exists(image_path):
        logger.error(f"Source image not found: {image_path}")
        raise FileNotFoundError(f"Source image '{image_path}' does not exist.")
        
    filename = os.path.basename(image_path)
    
    # Fallback to today's date if not provided
    if not record_date:
        record_date = datetime.now().strftime("%Y-%m-%d")
        
    # Standardize balanced status strings
    is_balanced = status.lower() in ["verified", "balanced", "valid", "success"]
    
    if is_balanced:
        target_dir = SUCCESSFULLY_IMPORTED
        new_filename = f"{record_date}_{filename}"
    else:
        target_dir = REQUIRES_REVIEW
        new_filename = f"{record_date}_review_{filename}"
        
    target_path = os.path.join(target_dir, new_filename)
    
    # Handle duplicate collisions to avoid overwriting historical ledger images
    counter = 1
    base_name, ext = os.path.splitext(new_filename)
    while os.path.exists(target_path):
        target_path = os.path.join(target_dir, f"{base_name}_{counter}{ext}")
        counter += 1
        
    logger.info(f"Archiving processed file: Moving '{image_path}' to '{target_path}' (Status: {status})")
    shutil.move(image_path, target_path)
    
    return target_path
