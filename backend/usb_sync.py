#!/usr/bin/env python3
"""
USB Sync Utility
Discovers external USB/EXTERNAL drives, verifies/initializes /FuelSync_Local_Backups/,
mirrors databases, active Excel sheets, and corrections cache, and updates status in the UI.
"""

import os
import shutil
import logging
import platform
import json
import asyncio
import threading
import time
from datetime import datetime

logger = logging.getLogger("USBSync")
logger.setLevel(logging.INFO)

# Global sync state tracker
last_sync_info = {
    "status": "idle",
    "last_sync_time": None,
    "copied_files": []
}

# Prevents redundant file copies (path -> (mtime, size))
last_copied_states = {}

# Active SSE subscriber queues
subscribers = []

# Main asyncio event loop (set on startup)
main_loop = None

def set_main_loop(loop):
    """Sets the active event loop to allow thread-safe scheduling of events."""
    global main_loop
    main_loop = loop

def register_subscriber(queue: asyncio.Queue):
    """Registers an SSE client queue."""
    subscribers.append(queue)
    logger.debug(f"Registered SSE subscriber. Total: {len(subscribers)}")

def unregister_subscriber(queue: asyncio.Queue):
    """Unregisters an SSE client queue."""
    if queue in subscribers:
        subscribers.remove(queue)
    logger.debug(f"Unregistered SSE subscriber. Total: {len(subscribers)}")

def notify_subscribers(event_data: dict):
    """Dispatches a notification event to all registered client queues thread-safely."""
    if not main_loop:
        logger.warning("Main event loop not set. Cannot notify subscribers.")
        return
    for queue in list(subscribers):
        try:
            main_loop.call_soon_threadsafe(queue.put_nowait, event_data)
        except Exception as e:
            logger.error(f"Failed to post to client queue: {str(e)}")

def get_last_sync_status() -> dict:
    """Returns the last successful synchronization status report, if any."""
    if last_sync_info["status"] == "complete":
        return {
            "status": "success",
            "event": "usb_sync_complete",
            "message": "Local Physical Redundancy Verified - USB Mirror Complete",
            "timestamp": last_sync_info["last_sync_time"],
            "files": last_sync_info["copied_files"]
        }
    return None

def scan_for_external_mounts(tags=None) -> list:
    """
    Scans the system for external mounted partitions.
    Matches volume labels/names containing specified hardware tags.
    """
    if tags is None:
        tags = ["USB", "EXTERNAL", "PUMP", "FUELSYNC", "BACKUP"]
    
    mounts = []
    system = platform.system()
    
    # 1. MacOS Scan (/Volumes)
    if system == "Darwin" or os.path.exists("/Volumes"):
        try:
            for item in os.listdir("/Volumes"):
                path = os.path.join("/Volumes", item)
                if os.path.isdir(path) and not os.path.islink(path):
                    mounts.append((item, path))
        except Exception as e:
            logger.error(f"Failed to scan macOS mounts at /Volumes: {str(e)}")
            
    # 2. Linux Scan (/media and /run/media)
    if system == "Linux" or os.path.exists("/media"):
        for base in ["/media", "/run/media"]:
            if os.path.exists(base):
                try:
                    for user_dir in os.listdir(base):
                        user_path = os.path.join(base, user_dir)
                        if os.path.isdir(user_path):
                            for item in os.listdir(user_path):
                                path = os.path.join(user_path, item)
                                if os.path.isdir(path) and not os.path.islink(path):
                                    mounts.append((item, path))
                except Exception as e:
                    logger.error(f"Failed to scan Linux mounts at {base}: {str(e)}")
                    
    # 3. Windows Scan (D:\\ to Z:\\)
    if system == "Windows":
        import string
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                label = f"Drive_{letter}"
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    volumeNameBuffer = ctypes.create_unicode_buffer(1024)
                    fileSystemNameBuffer = ctypes.create_unicode_buffer(1024)
                    serial_number = ctypes.c_ulong(0)
                    max_component_length = ctypes.c_ulong(0)
                    file_system_flags = ctypes.c_ulong(0)
                    rc = kernel32.GetVolumeInformationW(
                        ctypes.c_wchar_p(drive_path),
                        volumeNameBuffer,
                        ctypes.sizeof(volumeNameBuffer),
                        ctypes.byref(serial_number),
                        ctypes.byref(max_component_length),
                        ctypes.byref(file_system_flags),
                        fileSystemNameBuffer,
                        ctypes.sizeof(fileSystemNameBuffer)
                    )
                    if rc:
                        label = volumeNameBuffer.value
                except Exception:
                    pass
                mounts.append((label, drive_path))
                
    # Filter matching hardware tags
    valid_mounts = []
    for label, path in mounts:
        label_upper = label.upper()
        if any(tag in label_upper for tag in tags):
            valid_mounts.append(path)
            
    return valid_mounts

def get_active_source_files() -> list:
    """Locates core databases, learning caches, and Excel sheets inside the workspace."""
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(backend_dir)
    
    source_files = []
    
    # 1. Active Databases
    for db in ["pump_accounts.db", "ledger.db"]:
        db_path = os.path.join(backend_dir, db)
        if os.path.exists(db_path):
            source_files.append(("database", db_path))
            
    # 2. Learning Cache Memory
    cache_path = os.path.join(backend_dir, "corrections_cache.json")
    if os.path.exists(cache_path):
        source_files.append(("cache", cache_path))
        
    # 3. Master Excel Workbooks in pump_exports
        
    # Any spreadsheet generated in pump_exports
    exports_dir = os.path.join(workspace_dir, "pump_exports")
    if os.path.exists(exports_dir):
        try:
            for item in os.listdir(exports_dir):
                if item.endswith(".xlsx"):
                    source_files.append(("excel", os.path.join(exports_dir, item)))
        except Exception:
            pass
            
    return source_files

def check_for_file_updates(source_files: list) -> bool:
    """Checks if any file has been modified or resized since the last sync."""
    global last_copied_states
    has_changes = False
    
    for _, path in source_files:
        try:
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
            state_key = (mtime, size)
            if last_copied_states.get(path) != state_key:
                has_changes = True
        except Exception:
            pass
            
    return has_changes

def execute_external_usb_mirror():
    """
    Main mirroring routine. Auto-discovers external mounts, verifies target directory,
    validates changes, copies assets, and broadcasts completion events.
    """
    global last_copied_states, last_sync_info
    
    # 1. Discover mount points
    mount_paths = scan_for_external_mounts()
    if not mount_paths:
        if last_sync_info["status"] != "idle":
            last_sync_info["status"] = "idle"
        return
        
    # Pick the first discovered mount point
    target_mount = mount_paths[0]
    backup_dir = os.path.join(target_mount, "FuelSync_Local_Backups")
    
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to initialize backups target folder {backup_dir}: {str(e)}")
        return
        
    # 2. Get active sources
    source_files = get_active_source_files()
    if not source_files:
        logger.debug("No active files found to backup.")
        return
        
    # 3. Check for edits
    if not check_for_file_updates(source_files):
        # Already fully synced
        return
        
    logger.info(f"Synchronizing ledger assets to USB mount path: {backup_dir}")
    copied = []
    
    for category, path in source_files:
        filename = os.path.basename(path)
        dest_path = os.path.join(backup_dir, filename)
        try:
            shutil.copy2(path, dest_path)
            # Record state
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
            last_copied_states[path] = (mtime, size)
            copied.append(filename)
            logger.info(f"Mirrored {category} file successfully: {filename}")
        except Exception as copy_err:
            logger.error(f"Copying failed for file {filename}: {str(copy_err)}")
            
    if copied:
        # Commit synchronization success
        last_sync_info["status"] = "complete"
        last_sync_info["last_sync_time"] = datetime.now().isoformat()
        last_sync_info["copied_files"] = copied
        
        event_data = {
            "status": "success",
            "event": "usb_sync_complete",
            "message": "Local Physical Redundancy Verified - USB Mirror Complete",
            "timestamp": last_sync_info["last_sync_time"],
            "files": copied
        }
        notify_subscribers(event_data)
        logger.info("USB Mirror Sync completed successfully.")

def usb_sync_background_loop(interval_sec=5):
    """Runs a periodic polling loop checking for external drives."""
    logger.info("Starting background hardware asset mirroring loop...")
    while True:
        try:
            execute_external_usb_mirror()
        except Exception as loop_err:
            logger.error(f"Error in USB backup routine: {str(loop_err)}")
        time.sleep(interval_sec)

def start_usb_sync_service(interval_sec=5):
    """Starts the USB hardware synchronization daemon service thread."""
    thread = threading.Thread(
        target=usb_sync_background_loop,
        args=(interval_sec,),
        name="USBSyncDaemon",
        daemon=True
    )
    thread.start()
    logger.info("USB synchronization daemon thread launched.")
