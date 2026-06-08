import os
import zipfile
import logging
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Backup")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUPS_DIR = os.path.join(BACKEND_DIR, "backups")
WORKSPACE_DIR = os.path.dirname(BACKEND_DIR)

from pathlib import Path
from dotenv import load_dotenv

# Explicitly find root directory .env
_root_dir = Path(__file__).resolve().parent.parent
_root_env = _root_dir / ".env"
if _root_env.exists():
    load_dotenv(dotenv_path=_root_env)
else:
    load_dotenv() # Fallback to local

_export_path = os.getenv("EXPORT_EXCEL_PATH")
if not _export_path:
    EXCEL_BACKUP_PATH = os.path.join(WORKSPACE_DIR, "pump_exports", "Pump_Accounts.xlsx")
else:
    if not os.path.isabs(_export_path):
        EXCEL_BACKUP_PATH = os.path.abspath(os.path.join(WORKSPACE_DIR, _export_path))
    else:
        EXCEL_BACKUP_PATH = _export_path

FILES_TO_BACKUP = [
    # SQLite active DB files
    os.path.join(BACKEND_DIR, "ledger.db"),
    os.path.join(BACKEND_DIR, "pump_accounts.db"), # secondary database name configuration
    # Excel active ledgers
    EXCEL_BACKUP_PATH
]

def execute_local_backup():
    """
    Creates a compressed timestamped .zip archive containing active SQLite database files and master Excel ledger,
    and applies a rolling 30-day retention cleanup.
    """
    logger.info("Executing automated local backup sequence...")
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    
    # Standardize files that actually exist on disk
    existing_files = [f for f in FILES_TO_BACKUP if os.path.exists(f)]
    if not existing_files:
        logger.warning("No active ledger or database assets found for backup.")
        return None
        
    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_filename = f"backup_{timestamp}.zip"
    zip_path = os.path.join(BACKUPS_DIR, zip_filename)
    
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for f in existing_files:
                # Store in zip using its base name
                zipf.write(f, os.path.basename(f))
                logger.info(f"Appended to backup archive: {os.path.basename(f)}")
                
        logger.info(f"Automated backup completed triumphantly: {zip_path}")
        
        # Apply 30-day rolling retention cleanup
        apply_rolling_retention()
        return zip_path
        
    except Exception as e:
        logger.error(f"Backup execution failed: {str(e)}")
        return None

def apply_rolling_retention():
    """
    Scans the backups directory and automatically deletes any backup files older than 30 days.
    """
    logger.info("Enforcing rolling 30-day retention policy...")
    try:
        now = datetime.now()
        retention_limit = now - timedelta(days=30)
        deleted_count = 0
        
        if not os.path.exists(BACKUPS_DIR):
            return
            
        for filename in os.listdir(BACKUPS_DIR):
            if filename.startswith("backup_") and filename.endswith(".zip"):
                filepath = os.path.join(BACKUPS_DIR, filename)
                
                # Try parsing timestamp from filename, e.g. backup_YYYY-MM-DD_HHMMSS.zip
                is_old = False
                try:
                    # Strip 'backup_' and '.zip'
                    time_str = filename[7:-4]
                    file_time = datetime.strptime(time_str, "%Y-%m-%d_%H%M%S")
                    if file_time < retention_limit:
                        is_old = True
                except Exception:
                    # Fallback to file modification time
                    mtime = os.path.getmtime(filepath)
                    file_time = datetime.fromtimestamp(mtime)
                    if file_time < retention_limit:
                        is_old = True
                        
                if is_old:
                    logger.info(f"Removing expired backup file: {filename} (Dated: {file_time})")
                    os.remove(filepath)
                    deleted_count += 1
                    
        if deleted_count > 0:
            logger.info(f"Successfully pruned {deleted_count} expired backups.")
        else:
            logger.info("Retention sweep completed. No backups required pruning.")
            
    except Exception as err:
        logger.error(f"Failed to enforce rolling retention cleanup: {str(err)}")
