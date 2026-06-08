#!/usr/bin/env python3
"""
PumpAI Desktop Application Compilation Script
Uses PyInstaller to compile the complete FastAPI backend workspace into a single executable.
"""

import os
import sys
import subprocess
import shutil

def check_and_install_pyinstaller():
    """Checks if pyinstaller is installed in the active environment, and installs it if missing."""
    try:
        import PyInstaller
        print("✓ PyInstaller is already installed.")
    except ImportError:
        print("⚠ PyInstaller is missing. Installing PyInstaller programmatically...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
            print("✓ PyInstaller installed successfully.")
        except Exception as e:
            print(f"❌ Failed to install PyInstaller: {str(e)}")
            sys.exit(1)

def build_app():
    # 1. Ensure we are in the correct directory
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(backend_dir)
    print(f"Working directory set to: {backend_dir}")

    # 2. Check PyInstaller dependency
    check_and_install_pyinstaller()

    import PyInstaller.__main__

    # 3. Configure assets and rules to include in the binary package
    # Use cross-platform path separator (':' on Unix, ';' on Windows)
    sep = os.pathsep
    
    # Define default folder layouts to create and package
    default_folders = [
        "uploaded_raw_photos",
        "processed_images",
        "logs",
        "historical_register_photos",
        "flagged_records",
        "backups"
    ]
    
    # Ensure they exist and contain a placeholder so PyInstaller packages them
    for folder in default_folders:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, ".gitkeep"), "w") as f:
            f.write("")
            
    # Include deterministic_rules.json, init_db.py, and default folder layouts inside the compiled runtime path
    assets = [
        f"deterministic_rules.json{sep}.",
        f"init_db.py{sep}."
    ]
    for folder in default_folders:
        assets.append(f"{folder}{sep}{folder}")

    print("Building application with the following configurations:")
    print("  - Target Entry: main.py")
    print("  - Package Mode: --onefile (Single executable package)")
    print("  - Window Mode: --noconsole (Windowless/Headless execution)")
    print("  - Included Assets:")
    for asset in assets:
        print(f"      * {asset}")

    # 4. Formulate PyInstaller command arguments
    pyinstaller_args = [
        "main.py",
        "--onefile",
        "--noconsole",
        "--name=PumpAI_Backend",
        "--clean"
    ]

    # Append all assets to command line arguments
    for asset in assets:
        pyinstaller_args.extend(["--add-data", asset])

    # 5. Run the compiler
    print("\n🚀 Starting PyInstaller binary compilation pass...")
    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n=======================================================")
        print("🎉 DESKTOP APPLICATION COMPILED SUCCESSFULLY!")
        print("=======================================================")
        
        # Output locations
        dist_dir = os.path.join(backend_dir, "dist")
        exec_name = "PumpAI_Backend" + (".exe" if sys.platform.startswith("win") else "")
        exec_path = os.path.join(dist_dir, exec_name)
        
        print(f"✓ Output Directory : {dist_dir}")
        if os.path.exists(exec_path):
            print(f"✓ Binary Executable: {exec_path}")
            print(f"✓ Size             : {os.path.getsize(exec_path) / (1024*1024):.2f} MB")
        else:
            print(f"⚠ Build finished, but binary was not found at expected location: {exec_path}")
            print("  Check the build/ or dist/ directories manually.")
            
        print("=======================================================\n")
    except Exception as e:
        print(f"\n❌ PyInstaller compilation failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    build_app()
