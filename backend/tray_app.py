import sys
import subprocess
import os
import signal
import platform
import time
import threading
import tempfile
import webbrowser
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

# Import PumpAI Unified Logger
try:
    from logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("PumpAI")

# Import Backup/Sync Routines
try:
    from backup import execute_local_backup
except ImportError:
    def execute_local_backup():
        logger.error("backup.py not found in path.")
        return None

try:
    from usb_sync import execute_external_usb_mirror
except ImportError:
    def execute_external_usb_mirror():
        logger.error("usb_sync.py not found in path.")

# Global subprocess handles
backend_proc = None
frontend_proc = None

def start_backend(sys_executable, backend_dir):
    logger.info("Launching FastAPI backend server...")
    env = os.environ.copy()
    kwargs = {
        "cwd": backend_dir,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
        
    cmd = [sys_executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
    return subprocess.Popen(cmd, **kwargs)

def start_frontend(sys_executable, repo_root, port="3000", host="127.0.0.1"):
    logger.info("Launching frontend static server...")
    import shutil
    import json
    
    has_npm = shutil.which("npm") is not None
    package_json_path = os.path.join(repo_root, "package.json")
    has_dev_script = False
    
    if has_npm and os.path.exists(package_json_path):
        try:
            with open(package_json_path, 'r', encoding='utf-8') as f:
                pkg = json.load(f)
                has_dev_script = "dev" in pkg.get("scripts", {})
        except Exception:
            pass
            
    if has_dev_script:
        node_modules_path = os.path.join(repo_root, "node_modules")
        if not os.path.exists(node_modules_path):
            logger.info("node_modules missing. Installing npm packages...")
            try:
                subprocess.run(["npm", "install", "--silent"], cwd=repo_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                logger.warning(f"npm install failed: {str(e)}")
        
        use_shell = (platform.system() == "Windows")
        try:
            return subprocess.Popen(
                ["npm", "run", "dev", "--", "--port", port, "--host", host],
                cwd=repo_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=use_shell
            )
        except Exception as e:
            logger.warning(f"Failed to start npm dev server: {str(e)}. Falling back to http.server.")
            
    # Fallback to python http.server
    return subprocess.Popen(
        [sys_executable, "-m", "http.server", port, "--bind", host, "--directory", repo_root],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def terminate_process(proc):
    if proc is None:
        return
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

def cleanup_servers():
    global backend_proc, frontend_proc
    logger.info("Shutting down background processes cleanly...")
    
    if backend_proc:
        terminate_process(backend_proc)
        backend_proc = None
        
    if frontend_proc:
        terminate_process(frontend_proc)
        frontend_proc = None

def signal_handler(signum, frame):
    cleanup_servers()
    sys.exit(0)

# Setup process cleanup signals
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def trigger_backup_now():
    def _run():
        logger.info("Background manual backup sequence triggered via system tray.")
        try:
            zip_path = execute_local_backup()
            if zip_path:
                logger.info(f"Local backup created successfully: {zip_path}")
            else:
                logger.warning("Local backup execution returned None.")
            
            execute_external_usb_mirror()
            logger.info("Background USB duplication finished.")
        except Exception as e:
            logger.error(f"Error running database backup: {str(e)}")
            
    threading.Thread(target=_run, name="TrayBackupThread", daemon=True).start()

def view_logs():
    try:
        backend_dir = os.path.dirname(os.path.abspath(__file__))
        log_file_path = os.path.join(backend_dir, "logs", "pipeline.log")
        
        # Create log directory if it does not exist
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        # Touch log file if it doesn't exist
        if not os.path.exists(log_file_path):
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.write("")
                
        if platform.system() == "Darwin":
            # macOS: Write a temporary .sh script and open it with Terminal.app
            temp_script = tempfile.NamedTemporaryFile(suffix=".sh", delete=False)
            script_content = (
                f"#!/bin/bash\n"
                f"clear\n"
                f"echo '==========================================================='\n"
                f"echo '       FuelSync / PumpAI — Active Log Viewer (Last 15 lines)'\n"
                f"echo '==========================================================='\n"
                f"echo\n"
                f"tail -n 15 '{log_file_path}'\n"
                f"echo\n"
                f"echo '==========================================================='\n"
                f"read -p 'Press [Enter] to close this window...' dummy\n"
            )
            temp_script.write(script_content.encode('utf-8'))
            temp_script.close()
            os.chmod(temp_script.name, 0o755)
            
            # Open Terminal.app to run the script
            subprocess.Popen(["open", "-a", "Terminal", temp_script.name])
            
        elif platform.system() == "Windows":
            # Windows: Write a temporary .bat script and start it in a new CMD window
            temp_script = tempfile.NamedTemporaryFile(suffix=".bat", delete=False)
            escaped_path = log_file_path.replace('\\', '\\\\')
            script_content = (
                f"@echo off\n"
                f"cls\n"
                f"echo ===========================================================\n"
                f"echo        FuelSync / PumpAI - Active Log Viewer (Last 15 lines)\n"
                f"echo ===========================================================\n"
                f"echo.\n"
                f"\"{sys.executable}\" -c \"import sys; f=open('{escaped_path}', encoding='utf-8'); print(''.join(f.readlines()[-15:]))\"\n"
                f"echo.\n"
                f"echo ===========================================================\n"
                f"pause\n"
            )
            temp_script.write(script_content.encode('utf-8'))
            temp_script.close()
            
            # Run start command
            subprocess.Popen(["cmd.exe", "/c", "start", temp_script.name])
        else:
            logger.warning("Log viewer not supported on this platform.")
    except Exception as e:
        logger.error(f"Failed to view active status/logs: {str(e)}")

def create_image():
    width = 64
    height = 64
    image = Image.new('RGBA', (width, height), color=(0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    
    # Sleek dark rounded rectangle background
    dc.rounded_rectangle([2, 2, 61, 61], radius=14, fill=(30, 41, 59), outline=(16, 185, 129), width=3)
    
    # Outer green ring
    dc.ellipse([16, 16, 47, 47], fill=(16, 185, 129))
    
    # Glowing yellow/amber inner circle
    dc.ellipse([24, 24, 39, 39], fill=(245, 158, 11))
    
    return image

def main():
    global backend_proc, frontend_proc
    
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(backend_dir)
    sys_executable = sys.executable
    
    # Start background processes
    backend_proc = start_backend(sys_executable, backend_dir)
    frontend_proc = start_frontend(sys_executable, repo_root)
    
    logger.info("PumpAI background servers launched.")
    
    # Define menu callbacks
    def on_open_workspace(icon, item):
        webbrowser.open("http://127.0.0.1:3000")
        
    def on_trigger_backup(icon, item):
        trigger_backup_now()
        
    def on_view_logs(icon, item):
        view_logs()
        
    def on_exit(icon, item):
        cleanup_servers()
        icon.stop()
        
    # Build context menu
    menu = pystray.Menu(
        item('Open FuelSync Workspace', on_open_workspace, default=True),
        item('Trigger DB Backup Now', on_trigger_backup),
        item('View Active Status/Logs', on_view_logs),
        item('Exit Application', on_exit)
    )
    
    # Initialize and run tray icon (this blocks on macOS main thread)
    try:
        icon = pystray.Icon("FuelSync", create_image(), "FuelSync Monitor", menu)
        icon.run()
    except Exception as e:
        logger.error(f"Failed to initialize system tray icon: {str(e)}")
        logger.info("Running in headless/fallback mode. Press Ctrl+C to terminate.")
        # Keep process alive until termination signal or interrupt
        while True:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                break
        cleanup_servers()

if __name__ == "__main__":
    main()
