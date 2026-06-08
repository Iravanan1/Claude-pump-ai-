"""
Automated Data Encryption Wrapper for SQLite Storage Layer.
Exposes tools to securely encrypt and decrypt sensitive financial fields
(amount, party_name, cash_short_or_over) at the database layer.
Supports transparent backward compatibility for plain/unencrypted rows.
"""

import os
import base64
import logging
import secrets
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from dotenv import load_dotenv

# Setup logger
logger = logging.getLogger("CryptoVault")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BACKEND_DIR, ".env")

# Ensure .env is loaded
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
else:
    load_dotenv()

_fernet = None

def _load_or_generate_key() -> bytes:
    """
    Loads PUMP_AI_MASTER_KEY from .env or system environment.
    Generates and saves a random key to .env if none is found.
    """
    master_key = os.environ.get("PUMP_AI_MASTER_KEY")
    if not master_key:
        logger.warning("PUMP_AI_MASTER_KEY not found in environment. Generating a secure key...")
        master_key = secrets.token_urlsafe(32)
        # Write to .env
        try:
            # Check if .env file exists to format nicely
            prefix = ""
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r") as f:
                    content = f.read()
                if content and not content.endswith("\n"):
                    prefix = "\n"
            
            with open(ENV_PATH, "a") as f:
                f.write(f"{prefix}PUMP_AI_MASTER_KEY={master_key}\n")
            
            os.environ["PUMP_AI_MASTER_KEY"] = master_key
            logger.warning(f"Generated secure master key and appended to {ENV_PATH}")
        except Exception as e:
            logger.error(f"Failed to write generated master key to .env: {str(e)}")
            
    # Derive valid 32-byte Fernet key using PBKDF2HMAC
    password = master_key.encode()
    salt = b"pump_ai_secure_salt_value_987654321"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    derived_bytes = kdf.derive(password)
    return base64.urlsafe_b64encode(derived_bytes)

def get_fernet() -> Fernet:
    """
    Gets the singleton cached Fernet instance.
    """
    global _fernet
    if _fernet is None:
        key = _load_or_generate_key()
        _fernet = Fernet(key)
    return _fernet

def is_encrypted(value: str) -> bool:
    """
    Checks if a string is a valid Fernet token.
    """
    if not isinstance(value, str):
        return False
    if not value.startswith("gAAAAAB"):
        return False
    try:
        # Quick trial decryption
        get_fernet().decrypt(value.encode())
        return True
    except Exception:
        return False

def encrypt_field(value) -> str:
    """
    Encrypts a field value (string, float, int, etc.) and returns the base64 ciphertext.
    If the value is already encrypted or None, returns it untouched.
    """
    if value is None:
        return None
    
    val_str = str(value)
    if is_encrypted(val_str):
        return val_str
        
    try:
        fernet = get_fernet()
        return fernet.encrypt(val_str.encode()).decode()
    except Exception as e:
        logger.error(f"Failed to encrypt field: {str(e)}")
        return val_str

def decrypt_field(value, return_type=str):
    """
    Decrypts a Fernet token and casts to return_type (str, float, etc.).
    If not encrypted, returns value cast to return_type.
    None values remain None.
    """
    if value is None:
        return None
        
    val_str = str(value)
    if not is_encrypted(val_str):
        # Backward compatibility: return raw cast value
        try:
            if return_type in (float, int):
                if val_str.strip() in ("", "None"):
                    return return_type(0)
                return return_type(float(val_str))
            return return_type(value)
        except Exception:
            return value
            
    try:
        fernet = get_fernet()
        decrypted_bytes = fernet.decrypt(val_str.encode())
        decrypted_str = decrypted_bytes.decode()
        if return_type in (float, int):
            if decrypted_str.strip() in ("", "None"):
                return return_type(0)
            return return_type(float(decrypted_str))
        return return_type(decrypted_str)
    except Exception as e:
        logger.error(f"Failed to decrypt field value: {str(e)}")
        return value

def encrypt_raw_data(data: dict) -> dict:
    """
    Recursively scans raw_data dictionary and encrypts sensitive keys:
    'party_name', 'amount', and 'cash_short_or_over' at the field level.
    """
    if not isinstance(data, dict):
        return data
        
    new_data = {}
    for k, v in data.items():
        if k in ("party_name", "amount", "cash_short_or_over"):
            new_data[k] = encrypt_field(v)
        elif isinstance(v, list):
            new_data[k] = [encrypt_raw_data(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, dict):
            new_data[k] = encrypt_raw_data(v)
        else:
            new_data[k] = v
    return new_data

def decrypt_raw_data(data: dict) -> dict:
    """
    Recursively scans raw_data dictionary and decrypts sensitive keys.
    """
    if not isinstance(data, dict):
        return data
        
    new_data = {}
    for k, v in data.items():
        if k == "party_name":
            new_data[k] = decrypt_field(v, return_type=str)
        elif k in ("amount", "cash_short_or_over"):
            new_data[k] = decrypt_field(v, return_type=float)
        elif isinstance(v, list):
            new_data[k] = [decrypt_raw_data(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, dict):
            new_data[k] = decrypt_raw_data(v)
        else:
            new_data[k] = v
    return new_data
