"""Password hashing, session tokens and at-rest encryption of switch credentials."""
import base64
import hashlib
import hmac
import os
import secrets
import time

from . import config, db

try:  # Preferred: authenticated encryption via the `cryptography` package.
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None

SESSION_TTL = 12 * 3600


def _key() -> bytes:
    if not os.path.exists(config.SECRET_KEY_PATH):
        key = Fernet.generate_key() if Fernet else base64.urlsafe_b64encode(os.urandom(32))
        with open(config.SECRET_KEY_PATH, "wb") as f:
            f.write(key)
        os.chmod(config.SECRET_KEY_PATH, 0o600)
    with open(config.SECRET_KEY_PATH, "rb") as f:
        return f.read().strip()


def encrypt(plain: str) -> str:
    if Fernet:
        return "f1:" + Fernet(_key()).encrypt(plain.encode()).decode()
    # Fallback: XOR stream from SHA-256 keystream + HMAC. Install `cryptography` for production use.
    k = _key()
    nonce = os.urandom(16)
    data = plain.encode()
    stream = b""
    i = 0
    while len(stream) < len(data):
        stream += hashlib.sha256(k + nonce + i.to_bytes(4, "big")).digest()
        i += 1
    ct = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(k, nonce + ct, hashlib.sha256).digest()[:16]
    return "x1:" + base64.urlsafe_b64encode(nonce + tag + ct).decode()


def decrypt(token: str) -> str:
    if token.startswith("f1:"):
        return Fernet(_key()).decrypt(token[3:].encode()).decode()
    if token.startswith("x1:"):
        k = _key()
        raw = base64.urlsafe_b64decode(token[3:])
        nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
        if not hmac.compare_digest(tag, hmac.new(k, nonce + ct, hashlib.sha256).digest()[:16]):
            raise ValueError("credential integrity check failed")
        stream = b""
        i = 0
        while len(stream) < len(ct):
            stream += hashlib.sha256(k + nonce + i.to_bytes(4, "big")).digest()
            i += 1
        return bytes(a ^ b for a, b in zip(ct, stream)).decode()
    raise ValueError("unknown secret format")


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 310_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 310_000)
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


ROLES = ("viewer", "operator", "admin")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
DEFAULT_ADMIN_PASSWORD = "admin"
TACACS_DEFAULTS = {"enabled": False, "servers": [], "key": "", "mode": "pap", "timeout": 5,
                   "authorize": True, "service": "shell", "admin_level": 15, "operator_level": 7,
                   "default_role": "viewer", "local_fallback": "always"}


class LoginError(Exception):
    pass


def ensure_admin():
    """Create the first admin account on a fresh install.

    Without SD_ADMIN_PASSWORD the account is admin / admin and must be changed at first login.
    Returns the password that was set (for the start-up banner), or None if users already exist.
    """
    if db.q1("SELECT 1 FROM users"):
        return None
    pw = config.ADMIN_PASSWORD or DEFAULT_ADMIN_PASSWORD
    db.x("INSERT INTO users(username, pwhash, role, source, must_change, created) VALUES (?,?,'admin','local',?,?)",
         (config.ADMIN_USER, hash_password(pw), 0 if config.ADMIN_PASSWORD else 1, time.time()))
    return pw


def tacacs_config():
    cfg = dict(TACACS_DEFAULTS)
    cfg.update(db.setting("tacacs", {}) or {})
    return cfg


# ---- brute-force throttle: 5 failures per user+IP in 5 minutes -> 5 minute lockout
_fails = {}


def _throttled(key):
    now = time.time()
    hits = [t for t in _fails.get(key, []) if now - t < 300]
    _fails[key] = hits
    return len(hits) >= 5


def _fail(key):
    _fails.setdefault(key, []).append(time.time())


def password_policy(pw: str):
    if len(pw) < 10:
        raise LoginError("use at least 10 characters")
    if pw.lower() in ("admin", "password", "switchdeck", "changeme1234"):
        raise LoginError("pick a less common password")


def _start_session(username, ip):
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.x("INSERT INTO sessions(token, username, expires, ip, created) VALUES (?,?,?,?,?)",
         (token, username, now + SESSION_TTL, ip, now))
    db.x("UPDATE users SET last_login=? WHERE username=?", (now, username))
    db.x("DELETE FROM sessions WHERE expires < ?", (now,))
    return token


def login(username: str, password: str, ip: str = ""):
    """Authenticate against local accounts and/or TACACS+. Returns (token, user, method).

    Order, per Settings -> Authentication:
      local_fallback = "always"      local account first, then TACACS+
      local_fallback = "unreachable" TACACS+ first; local accounts only when no TACACS+ server answers
    """
    from . import tacacs
    username = (username or "").strip()
    key = f"{username.lower()}|{ip}"
    if not username or not password:
        raise LoginError("enter a username and password")
    if _throttled(key):
        raise LoginError("too many failed attempts; try again in 5 minutes")

    u = db.q1("SELECT * FROM users WHERE username=?", (username,))
    cfg = tacacs_config()
    use_tacacs = cfg["enabled"] and cfg["servers"]

    def local_ok():
        return u and u["source"] == "local" and not u["disabled"] and check_password(password, u["pwhash"])

    if not use_tacacs or cfg["local_fallback"] == "always":
        if local_ok():
            return _start_session(username, ip), u, "local"
        if not use_tacacs or (u and u["source"] == "local"):
            _fail(key)
            raise LoginError("invalid username or password")

    try:
        r = tacacs.login(cfg, username, password, rem_addr=ip)
    except tacacs.Unreachable as e:
        db.event(None, "warning", f"TACACS+ unreachable during login for {username}: {e}", "auth")
        if local_ok():
            return _start_session(username, ip), u, "local (TACACS+ unreachable)"
        _fail(key)
        raise LoginError("authentication servers unreachable")
    except tacacs.TacacsError as e:
        db.event(None, "error", f"TACACS+ error during login for {username}: {e}", "auth")
        if local_ok():
            return _start_session(username, ip), u, "local (TACACS+ error)"
        raise LoginError("authentication server error; see Alerts & Events")
    if not r["ok"]:
        _fail(key)
        raise LoginError("invalid username or password")
    if u and u["disabled"]:
        raise LoginError("this account is disabled in SwitchDeck")
    if u and u["source"] == "local":
        raise LoginError("a local account with this name exists; rename one of them")
    role = tacacs.role_for(cfg, r["attrs"])
    if u:
        db.x("UPDATE users SET role=? WHERE username=?", (role, username))
    else:
        db.x("INSERT INTO users(username, pwhash, role, source, created) VALUES (?, '', ?, 'tacacs', ?)",
             (username, role, time.time()))
    u = db.q1("SELECT * FROM users WHERE username=?", (username,))
    return _start_session(username, ip), u, f"tacacs ({r['server']}, {role})"


def session_user(token: str):
    """Returns the user row for a live session, or None."""
    if not token:
        return None
    s = db.q1("SELECT u.* FROM sessions s JOIN users u ON u.username=s.username "
              "WHERE s.token=? AND s.expires>? AND u.disabled=0", (token, time.time()))
    return s


def end_user_sessions(username, keep_token=None):
    db.x("DELETE FROM sessions WHERE username=? AND token<>?", (username, keep_token or ""))


def create_api_token(name: str, user: str):
    """Read-only bearer token for integrations (e.g. Home Assistant dashboards). Shown once."""
    token = "sdk_" + secrets.token_urlsafe(30)
    db.x("INSERT INTO api_tokens(name, token_hash, prefix, created, created_by) VALUES (?,?,?,?,?)",
         (name, hashlib.sha256(token.encode()).hexdigest(), token[:10], time.time(), user))
    return token


def api_token_name(token: str):
    if not token or not token.startswith("sdk_"):
        return None
    r = db.q1("SELECT id, name FROM api_tokens WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    if not r:
        return None
    db.x("UPDATE api_tokens SET last_used=? WHERE id=?", (time.time(), r["id"]))
    return f"token:{r['name']}"


def logout(token: str):
    db.x("DELETE FROM sessions WHERE token=?", (token,))
