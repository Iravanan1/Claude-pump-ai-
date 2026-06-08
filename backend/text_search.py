import os
import re
import sqlite3
import logging
from typing import List

logger = logging.getLogger("TextSearch")
logging.basicConfig(level=logging.INFO)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BACKEND_DIR, "ledger.db")

def init_fts_db(db_path: str = DB_PATH) -> None:
    """
    Initializes the FTS5 virtual table named ledger_fts.
    """
    logger.info(f"Initializing ledger_fts in {db_path}...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS ledger_fts USING fts5(
            date,
            raw_transcription_text
        )
        """)
        conn.commit()
        conn.close()
        logger.info("Table 'ledger_fts' initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize 'ledger_fts' table: {str(e)}")
        raise e

def index_ledger_text(db_path: str, date_str: str, raw_text: str) -> None:
    """
    Safely indexes raw transcription text under a given date string.
    Deletes existing entries for the date to avoid duplicate records.
    """
    if not date_str or not raw_text or not raw_text.strip():
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Prevent duplicate entries by deleting old records for this date
        cursor.execute("DELETE FROM ledger_fts WHERE date = ?", (date_str.strip(),))
        
        cursor.execute("""
            INSERT INTO ledger_fts (date, raw_transcription_text)
            VALUES (?, ?)
        """, (date_str.strip(), raw_text.strip()))
        
        conn.commit()
        conn.close()
        logger.info(f"Successfully indexed raw transcription for date: {date_str}")
    except Exception as e:
        logger.error(f"Failed to index ledger text for date {date_str}: {str(e)}")

def search_ledger_fts(db_path: str, query_str: str) -> List[str]:
    """
    Executes a high-speed MATCH search against raw unstructured text.
    Returns a list of date strings where matches were found.
    """
    if not query_str or not query_str.strip():
        return []
        
    cleaned_query = query_str.strip()
    # Simple sanitization to prevent FTS5 syntax errors
    cleaned_query = re.sub(r'[^\w\s*]', ' ', cleaned_query)
    # Form prefix matching words: e.g., 'tractor' -> 'tractor*'
    search_terms = []
    for word in cleaned_query.split():
        if word:
            # If word already has asterisk, don't double append
            if word.endswith('*'):
                search_terms.append(word)
            else:
                search_terms.append(f"{word}*")
                
    match_query = " AND ".join(search_terms)
    
    if not match_query:
        return []

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date FROM ledger_fts 
            WHERE ledger_fts MATCH ?
        """, (match_query,))
        
        rows = cursor.fetchall()
        conn.close()
        
        # Return unique dates list ordered chronologically
        return sorted(list(set(r[0] for r in rows if r[0])))
    except Exception as e:
        logger.error(f"FTS search failed for query '{query_str}' using match '{match_query}': {str(e)}")
        return []
