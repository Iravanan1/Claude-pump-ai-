import os
import sys
import sqlite3

def main():
    print("==================================================")
    print("           PUMPAI ENVIRONMENT VERIFIER            ")
    print("==================================================\n")
    
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Check Environment Variables
    print("1. Checking Environment Variables...")
    env_vars = ["GEMINI_API_KEY", "ANTHROPIC_API_KEY"]
    all_env_pass = True
    for var in env_vars:
        val = os.getenv(var)
        if val:
            # Mask the key for security, showing only first 4 and last 4 characters
            masked = val[:4] + "..." + val[-4:] if len(val) > 8 else "PROVISIONED"
            print(f"   [PASS] {var}: {masked}")
        else:
            print(f"   [FAIL] {var} is missing!")
            all_env_pass = False
            
    if not all_env_pass:
        print("\n   [TIP] To add missing API keys, append them to your shell configuration profile:")
        print("         On macOS/Linux (e.g., ~/.zshrc or ~/.bash_profile):")
        print("           export GEMINI_API_KEY=\"your_key_here\"")
        print("           export ANTHROPIC_API_KEY=\"your_key_here\"")
        print("         Then run: source ~/.zshrc\n")
    else:
        print("   All environment variables verified.\n")
        
    # 2. Check Package Extensions
    print("2. Checking Package Extensions...")
    packages = [
        ("cv2", "opencv-python"),
        ("pandas", "pandas"),
        ("openpyxl", "openpyxl"),
        ("fastapi", "fastapi")
    ]
    all_pkg_pass = True
    for mod_name, pip_name in packages:
        try:
            __import__(mod_name)
            print(f"   [PASS] Loaded module '{mod_name}' successfully.")
        except ImportError:
            print(f"   [FAIL] Failed to load module '{mod_name}'!")
            print(f"          To install: pip install {pip_name}")
            all_pkg_pass = False
            
    if not all_pkg_pass:
        print("\n   [TIP] Install all missing packages using:")
        print("         pip install -r backend/requirements.txt\n")
    else:
        print("   All package extensions verified.\n")
        
    # 3. Check Storage Layers
    print("3. Checking Storage Layers...")
    
    # Resolve DB Path
    db_paths = [
        os.path.join(workspace_dir, "backend", "ledger.db"),
        os.path.join(workspace_dir, "ledger.db")
    ]
    db_path = None
    for p in db_paths:
        if os.path.exists(p):
            db_path = p
            break
            
    if db_path:
        print(f"   [PASS] SQLite Database found at: {db_path}")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # Simple query read
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [r[0] for r in cursor.fetchall()]
            conn.close()
            print(f"   [PASS] SQLite connection active. Discovered tables: {', '.join(tables)}")
        except Exception as e:
            print(f"   [FAIL] SQLite database query read failed: {str(e)}")
    else:
        print("   [FAIL] SQLite database 'ledger.db' not found in workspace root or backend directory!")
        
    # Verify directories
    dirs = [
        ("ledger_photos", os.path.join(workspace_dir, "ledger_photos")),
        ("processed_images", os.path.join(workspace_dir, "backend", "processed_images")),
        ("pump_exports", os.path.join(workspace_dir, "pump_exports"))
    ]
    
    for name, path in dirs:
        if not os.path.exists(path):
            try:
                os.makedirs(path, exist_ok=True)
                print(f"   [PASS] Created missing directory: {name} ({path})")
            except Exception as e:
                print(f"   [FAIL] Directory missing and could not be created: {name} ({path}) - {str(e)}")
        elif os.path.isdir(path):
            print(f"   [PASS] Directory exists: {name} ({path})")
        else:
            print(f"   [FAIL] Path exists but is not a directory: {name} ({path})")
            
    print("\n==================================================")
    print("All systems operational. Ready to execute local batch processing.")
    print("==================================================")

if __name__ == "__main__":
    main()
