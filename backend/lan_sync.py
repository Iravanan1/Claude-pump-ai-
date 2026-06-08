import os
import time
import hashlib
import asyncio
from typing import Set, Tuple
from logger import logger

# Resolve raw_backlog folder at repo root
BACKLOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "raw_backlog"))
os.makedirs(BACKLOG_DIR, exist_ok=True)

def save_to_backlog(file_bytes: bytes, original_filename: str = "") -> Tuple[str, str, int]:
    """
    Ingests file bytes, computes SHA-256 signature, and saves sequentially 
    into the raw_backlog directory. Preserves original extension (defaults to .jpg).
    Returns (saved_filename, sha256_hash, file_size).
    """
    sha256_hash = hashlib.sha256(file_bytes).hexdigest()
    
    # Extract file extension, fallback to .jpg if missing or invalid
    ext = ".jpg"
    if original_filename:
        _, file_ext = os.path.splitext(original_filename)
        if file_ext.lower() in [".jpg", ".jpeg", ".png", ".pdf"]:
            ext = file_ext.lower()
            
    # Generate sequential/unique filename
    timestamp = int(time.time())
    filename = f"sync_{timestamp}_{sha256_hash[:16]}{ext}"
    filepath = os.path.join(BACKLOG_DIR, filename)
    
    with open(filepath, "wb") as f:
        f.write(file_bytes)
        
    logger.info(f"Ingested sync media asset: {filename} (Size: {len(file_bytes)} bytes, SHA-256: {sha256_hash})")
    return filename, sha256_hash, len(file_bytes)

class SSEManager:
    """
    Manages active async queues for Server-Sent Events subscribers.
    """
    def __init__(self):
        self.listeners: Set[asyncio.Queue] = set()

    def add_listener(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.listeners.add(q)
        logger.info(f"SSE Listener registered. Total active listeners: {len(self.listeners)}")
        return q

    def remove_listener(self, q: asyncio.Queue):
        if q in self.listeners:
            self.listeners.remove(q)
            logger.info(f"SSE Listener disconnected. Total active listeners: {len(self.listeners)}")

    async def broadcast(self, event_data: dict):
        if not self.listeners:
            return
        logger.info(f"Broadcasting SSE event: {event_data} to {len(self.listeners)} listener(s)")
        # Make a copy of listeners to prevent modification during iteration
        for q in list(self.listeners):
            try:
                await q.put(event_data)
            except Exception as e:
                logger.warning(f"Failed to queue event for listener: {e}")

sse_manager = SSEManager()
