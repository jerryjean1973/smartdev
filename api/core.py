import base64
import hashlib
import hmac
import re
import secrets
import time

REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")

def password_digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()

def issue_session(key, now=None):
    expires = str(int(time.time() if now is None else now) + 28800)
    payload = expires + "." + secrets.token_hex(16)
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"

def valid_session(token, key, now=None):
    payload, _, signature = token.rpartition(".")
    expires, _, nonce = payload.partition(".")
    if (not re.fullmatch(r"[0-9]{1,12}", expires) or not re.fullmatch(r"[0-9a-f]{64}", signature)
            or ("." in payload and not re.fullmatch(r"[0-9a-f]{32}", nonce))):
        return False
    expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return int(expires) > (time.time() if now is None else now) and hmac.compare_digest(signature, expected)

def workspace_name(project_id):
    return "p-" + base64.b32encode(project_id.bytes).decode().rstrip("=").lower()

def project_token(key, project_id):
    return hmac.new(key, f"memory:{project_id}".encode(), hashlib.sha256).hexdigest()
