#!/usr/bin/env python3
"""
PumpAI Desktop Application Compilation Script
Uses PyInstaller to compile the complete FastAPI backend workspace into a single directory package.
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
    # 1. Ensure we are in the correct directory (backend/)
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(backend_dir)
    print(f"Working directory set to: {backend_dir}")

    # 2. Check PyInstaller dependency
    check_and_install_pyinstaller()

    import PyInstaller.__main__

    # 3. Configure assets and rules to include in the binary package
    sep = os.pathsep
    
    # Ensure default folders exist so PyInstaller can bundle them
    os.makedirs("processed_images", exist_ok=True)
    os.makedirs("../pump_exports", exist_ok=True)
    
    # Write a placeholder .gitkeep so PyInstaller packages the directory structure
    for folder in ["processed_images", "../pump_exports"]:
        with open(os.path.join(folder, ".gitkeep"), "w") as f:
            f.write("")

    # Map static structures inside relative runtime execution path
    assets = [
        f"deterministic_rules.json{sep}.",
        f"init_db.py{sep}.",
        f"processed_images{sep}processed_images",
        f"../pump_exports{sep}pump_exports"
    ]

    print("Building application with the following configurations:")
    print("  - Target Entry: main.py")
    print("  - Package Mode: --onedir (Directory package containing runtime and binaries)")
    print("  - Window Mode: --noconsole (Windowless/Headless execution)")
    print("  - Included Assets:")
    for asset in assets:
        print(f"      * {asset}")

    # 4. Formulate PyInstaller command arguments
    pyinstaller_args = [
        "main.py",
        "--onedir",
        "--noconsole",
        "--name=PumpAI_Backend",
        "--clean",
        "--distpath=dist",
        "--noconfirm"
    ]

    # Append all assets to command line arguments
    for asset in assets:
        pyinstaller_args.extend(["--add-data", asset])

    # Clean up any existing dist output directories to prevent lock/permissions errors
    dist_dir = os.path.join(backend_dir, "dist")
    for old_path in [os.path.join(dist_dir, "PumpAI_Backend"), os.path.join(dist_dir, "PumpAI_Backend.app")]:
        if os.path.exists(old_path):
            print(f"🧹 Removing old build directory: {old_path}")
            try:
                if os.path.isdir(old_path):
                    shutil.rmtree(old_path)
                else:
                    os.unlink(old_path)
            except Exception as clean_err:
                print(f"⚠ Warning: Could not clean {old_path}: {str(clean_err)}")

    # 5. Run the compiler
    print("\n🚀 Starting PyInstaller binary compilation pass...")
    try:
        PyInstaller.__main__.run(pyinstaller_args)
        print("\n=======================================================")
        print("🎉 DESKTOP APPLICATION COMPILED SUCCESSFULLY!")
        print("=======================================================")
        
        # Output locations
        dist_dir = os.path.join(backend_dir, "dist")
        package_dir = os.path.join(dist_dir, "PumpAI_Backend")
        exec_name = "PumpAI_Backend" + (".exe" if sys.platform.startswith("win") else "")
        exec_path = os.path.join(package_dir, exec_name)
        
        print(f"✓ Output Directory : {package_dir}")
        if os.path.exists(exec_path):
            print(f"✓ Binary Executable: {exec_path}")
            # Get size of folder
            total_size = 0
            for dirpath, dirnames, filenames in os.walk(package_dir):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        total_size += os.path.getsize(fp)
            print(f"✓ Package Size     : {total_size / (1024*1024):.2f} MB")
        else:
            print(f"⚠ Build finished, but binary was not found at expected location: {exec_path}")
            print("  Check the build/ or dist/ directories manually.")
            
        print("=======================================================\n")
    except Exception as e:
        print(f"\n❌ PyInstaller compilation failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    build_app()
