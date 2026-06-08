#!/usr/bin/env python3
"""
Multi-station profile and data directory workspace isolation manager.
Defines folder structure generation, dynamic module variable rebinding, and workspace switching.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger("PumpAI")

def get_active_profile() -> str:
    """Gets the active workspace profile name from runtime environment."""
    return os.environ.get("ACTIVE_WORKSPACE_PROFILE", "pump_station_1")

def initialize_active_workspace(profile_name: str):
    """
    Programmatically builds separate structural data sandboxes under the root directory:
      - /workspaces/[profile_name]/database/
      - /workspaces/[profile_name]/processed_images/
      - /workspaces/[profile_name]/pump_exports/
    """
    root_dir = Path(__file__).resolve().parent.parent
    workspace_dir = root_dir / "workspaces" / profile_name
    
    os.makedirs(workspace_dir / "database", exist_ok=True)
    os.makedirs(workspace_dir / "processed_images", exist_ok=True)
    os.makedirs(workspace_dir / "pump_exports", exist_ok=True)
    os.makedirs(workspace_dir / "pump_exports" / "charts", exist_ok=True)
    
    logger.info(f"Initialized isolated workspace tree for profile '{profile_name}' under {workspace_dir}")

def get_workspace_paths(profile_name: str) -> Dict[str, str]:
    """Resolves absolute paths for a given workspace profile name."""
    root_dir = Path(__file__).resolve().parent.parent
    workspace_dir = root_dir / "workspaces" / profile_name
    
    return {
        "database": str((workspace_dir / "database" / "pump_accounts.db").resolve()),
        "processed_images": str((workspace_dir / "processed_images").resolve()),
        "pump_exports": str((workspace_dir / "pump_exports").resolve()),
        "excel_path": str((workspace_dir / "pump_exports" / "Pump_Accounts.xlsx").resolve()),
        "charts": str((workspace_dir / "pump_exports" / "charts").resolve()),
    }

def rebind_all_modules(profile_name: str):
    """
    Dynamically re-binds global variables in all loaded Python modules to the active profile's paths.
    """
    initialize_active_workspace(profile_name)
    paths = get_workspace_paths(profile_name)
    
    # Update environment variables
    os.environ["ACTIVE_WORKSPACE_PROFILE"] = profile_name
    os.environ["EXPORT_EXCEL_PATH"] = paths["excel_path"]
    
    # Scan loaded modules and re-bind path attributes
    for name, module in list(sys.modules.items()):
        if not module:
            continue
            
        # Re-bind DB path variables
        if hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", paths["database"])
        if hasattr(module, "DEFAULT_DB_PATH"):
            setattr(module, "DEFAULT_DB_PATH", paths["database"])
            
        # Re-bind Excel path variables
        if hasattr(module, "EXCEL_PATH"):
            setattr(module, "EXCEL_PATH", paths["excel_path"])
        if hasattr(module, "DEFAULT_EXCEL_PATH"):
            setattr(module, "DEFAULT_EXCEL_PATH", paths["excel_path"])
            
        # Re-bind processed images directory variables
        if hasattr(module, "PROCESSED_DIR"):
            setattr(module, "PROCESSED_DIR", paths["processed_images"])
        if hasattr(module, "processed_dir"):
            setattr(module, "processed_dir", paths["processed_images"])
            
        # Re-bind charts directory variables
        if hasattr(module, "CHARTS_DIR"):
            setattr(module, "CHARTS_DIR", paths["charts"])
        if hasattr(module, "charts_dir"):
            setattr(module, "charts_dir", paths["charts"])

def switch_active_workspace(profile_name: str):
    """
    Performs runtime workspace switching:
      1. Re-binds all module variables.
      2. Initializes/migrates the database of the new workspace.
      3. Re-routes FastAPI static files serving mounts dynamically.
    """
    rebind_all_modules(profile_name)
    
    # Lazy imports to prevent circular load issues
    import main
    import migrations
    
    # Re-initialize database schema
    main.init_db()
    migrations.apply_schema_updates(main.DB_PATH)
    
    # Rebind FastAPI static serving paths in-place
    for route in main.app.routes:
        if getattr(route, "name", None) == "processed_images":
            route.app.directory = main.processed_dir
            route.app.all_directories = [main.processed_dir]
        elif getattr(route, "name", None) == "charts":
            route.app.directory = main.charts_dir
            route.app.all_directories = [main.charts_dir]
            
    logger.info(f"Workspace successfully switched to profile '{profile_name}'")
