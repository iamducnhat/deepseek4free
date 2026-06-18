import base64
import hashlib
import uuid
from pathlib import Path

VAULT_DIR = Path.home() / ".canvas_sync_vault"

def get_machine_key():
    # Use UUID node (MAC address) + some salt to create a deterministic key for this machine
    mac = str(uuid.getnode())
    return hashlib.sha256((mac + "canvas_sync_salt_123").encode('utf-8')).digest()

def xor_crypt(data: bytes, key: bytes) -> bytes:
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))

def save_token(service: str, token: str):
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        VAULT_DIR.chmod(0o700)
    except Exception:
        pass
    
    key = get_machine_key()
    encrypted = xor_crypt(token.encode('utf-8'), key)
    encoded = base64.b64encode(encrypted).decode('utf-8')
    
    file_path = VAULT_DIR / f"{service}.dat"
    file_path.write_text(encoded, encoding='utf-8')
    try:
        file_path.chmod(0o600)
    except Exception:
        pass

def load_token(service: str) -> str:
    file_path = VAULT_DIR / f"{service}.dat"
    if not file_path.exists():
        return None
        
    try:
        encoded = file_path.read_text(encoding='utf-8')
        encrypted = base64.b64decode(encoded)
        key = get_machine_key()
        decrypted = xor_crypt(encrypted, key)
        return decrypted.decode('utf-8')
    except Exception:
        return None
