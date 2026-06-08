import os
import logging
from logging.handlers import RotatingFileHandler

# ANSI Color Escape Codes
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

class ColorFormatter(logging.Formatter):
    """
    ANSI Color-coded console status updates formatter.
    """
    def format(self, record):
        log_fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        if record.levelno == logging.INFO:
            fmt = f"{GREEN}{log_fmt}{RESET}"
        elif record.levelno == logging.WARNING:
            fmt = f"{YELLOW}{log_fmt}{RESET}"
        elif record.levelno >= logging.ERROR:
            fmt = f"{RED}{log_fmt}{RESET}"
        else:
            fmt = f"{CYAN}{log_fmt}{RESET}"
            
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)

# Resolve directories
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BACKEND_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOGS_DIR, "pipeline.log")

# Setup Unified PumpAI Logger
logger = logging.getLogger("PumpAI")
logger.setLevel(logging.DEBUG)
logger.propagate = False  # Avoid duplicate handlers output

# Clear existing handlers if any
if logger.hasHandlers():
    logger.handlers.clear()

# 1. Console Stream Handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(ColorFormatter())
logger.addHandler(console_handler)

# 2. Rolling File Handler (5MB, 3 backups max)
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024, # 5MB
    backupCount=3,
    encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

def log_pipeline_transaction(filename: str, execution_time: float, token_usage: int, math_passed: bool, exception_trace: str = None):
    """
    Structured pipeline logging schema recording OCR executions precisely.
    Records: Timestamp, File Name, Execution Time (seconds), Token Usage, Math Confidence Pass/Fail, and Exception Trace.
    """
    status = "PASS" if math_passed else "FAIL"
    msg = (
        f"TRANSACTION RECORD | "
        f"File: {filename} | "
        f"ExecTime: {execution_time:.3f}s | "
        f"Tokens: {token_usage} | "
        f"MathAudit: {status}"
    )
    if exception_trace:
        msg += f" | ExceptionTrace: {exception_trace}"
        
    if math_passed:
        logger.info(msg)
    else:
        logger.warning(msg)
