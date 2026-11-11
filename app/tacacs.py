"""Minimal TACACS+ client (RFC 8907), standard library only.

Supports:
  * Authentication: PAP (single START packet) or ASCII (START -> GETPASS -> CONTINUE)
  * Authorization: one REQUEST to read AV pairs (priv-lvl, or a custom switchdeck-role) for role mapping
  * Body obfuscation with the shared secret (MD5 pad, as the RFC specifies)
  * Multiple servers tried in order; "unreachable" is reported separately from "rejected"
"""
import hashlib
import os
import socket
import struct

TYPE_AUTHEN, TYPE_AUTHOR = 1, 2
VER_DEFAULT, VER_ONE = 0xC0, 0xC1          # minor 1 is required for PAP
FLAG_UNENCRYPTED = 0x01

AUTHEN_LOGIN = 1
AUTHEN_TYPE_ASCII, AUTHEN_TYPE_PAP = 1, 2
AUTHEN_SVC_LOGIN = 1
AUTHEN_PASS, AUTHEN_FAIL, AUTHEN_GETDATA, AUTHEN_GETUSER, AUTHEN_GETPASS, AUTHEN_RESTART, AUTHEN_ERROR = 1, 2, 3, 4, 5, 6, 7
AUTHOR_PASS_ADD, AUTHOR_PASS_REPL, AUTHOR_FAIL, AUTHOR_ERROR = 0x01, 0x02, 0x10, 0x11
AUTHEN_METH_TACACSPLUS = 0x06

PORT_NAME = b"switchdeck"


class TacacsError(Exception):
    """Protocol or configuration problem (bad key, malformed reply)."""


class Unreachable(TacacsError):
    """No server answered."""


def _pad(session_id: int, key: bytes, version: int, seq: int, length: int) -> bytes:
    out, prev = b"", b""
    base = struct.pack("!I", session_id) + key + bytes([version, seq])
    while len(out) < length:
        prev = hashlib.md5(base + prev).digest()
        out += prev
    return out[:length]


def _crypt(body: bytes, session_id: int, key: bytes, version: int, seq: int) -> bytes:
    if not key:
        return body
    return bytes(a ^ b for a, b in zip(body, _pad(session_id, key, version, seq, len(body))))


class _Session:
    def __init__(self, host, port, key, timeout):
        self.key = key.encode() if isinstance(key, str) else key
        self.session_id = struct.unpack("!I", os.urandom(4))[0]
        self.seq = 0
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise Unreachable(f"{host}:{port} {e}") from e
        self.sock.settimeout(timeout)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, ptype, version, body):
        self.seq += 1
        flags = 0 if self.key else FLAG_UNENCRYPTED
        hdr = struct.pack("!BBBBII", version, ptype, self.seq, flags, self.session_id, len(body))
        self.sock.sendall(hdr + _crypt(body, self.session_id, self.key, version, self.seq))

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise TacacsError("server closed the connection (wrong shared secret?)")
            buf += chunk
        return buf

    def recv(self):
        try:
            hdr = self._recv_exact(12)
        except socket.timeout as e:
            raise Unreachable("timed out waiting for reply") from e
        version, ptype, seq, flags, sid, length = struct.unpack("!BBBBII", hdr)
        if sid != self.session_id:
            raise TacacsError("session id mismatch")
        if length > 65536:
            raise TacacsError("reply too large (wrong shared secret?)")
        body = self._recv_exact(length)
        self.seq = seq
        if not flags & FLAG_UNENCRYPTED:
            body = _crypt(body, self.session_id, self.key, version, seq)
        return ptype, body


def _authen_start(user, password, rem_addr, authen_type):
    data = password.encode() if authen_type == AUTHEN_TYPE_PAP else b""
    u, r = user.encode(), rem_addr.encode()
    return struct.pack("!BBBBBBBB", AUTHEN_LOGIN, 1, authen_type, AUTHEN_SVC_LOGIN,
                       len(u), len(PORT_NAME), len(r), len(data)) + u + PORT_NAME + r + data


def _authen_reply(body):
    if len(body) < 6:
        raise TacacsError("short authentication reply (wrong shared secret?)")
    status, flags, msg_len, data_len = struct.unpack("!BBHH", body[:6])
    if status not in (1, 2, 3, 4, 5, 6, 7, 0x21) or 6 + msg_len + data_len != len(body):
        raise TacacsError("garbled authentication reply (wrong shared secret?)")
    return status, body[6:6 + msg_len].decode(errors="replace")


def authenticate(host, port, key, user, password, timeout=5, mode="pap", rem_addr=""):
    """Returns (ok: bool, server_message: str). Raises Unreachable / TacacsError."""
    s = _Session(host, port, key, timeout)
    try:
        if mode == "ascii":
            s.send(TYPE_AUTHEN, VER_DEFAULT, _authen_start(user, password, rem_addr, AUTHEN_TYPE_ASCII))
            for _ in range(4):
                _, body = s.recv()
                status, msg = _authen_reply(body)
                if status == AUTHEN_GETUSER:
                    reply = user.encode()
                elif status in (AUTHEN_GETPASS, AUTHEN_GETDATA):
                    reply = password.encode()
                else:
                    break
                s.send(TYPE_AUTHEN, VER_DEFAULT, struct.pack("!HHB", len(reply), 0, 0) + reply)
        else:
            s.send(TYPE_AUTHEN, VER_ONE, _authen_start(user, password, rem_addr, AUTHEN_TYPE_PAP))
            _, body = s.recv()
            status, msg = _authen_reply(body)
        if status == AUTHEN_ERROR:
            raise TacacsError(f"server error: {msg or 'no detail'}")
        return status == AUTHEN_PASS, msg
    finally:
        s.close()


def authorize(host, port, key, user, service="shell", timeout=5, mode="pap", rem_addr=""):
    """Returns (ok, {attr: value}) from an authorization REQUEST for `service`."""
    args = [f"service={service}".encode(), b"cmd="] if service == "shell" else [f"service={service}".encode()]
    u, r = user.encode(), rem_addr.encode()
    atype = AUTHEN_TYPE_PAP if mode == "pap" else AUTHEN_TYPE_ASCII
    body = struct.pack("!BBBBBBBB", AUTHEN_METH_TACACSPLUS, 1, atype, AUTHEN_SVC_LOGIN,
                       len(u), len(PORT_NAME), len(r), len(args))
    body += bytes(len(a) for a in args) + u + PORT_NAME + r + b"".join(args)
    s = _Session(host, port, key, timeout)
    try:
        s.send(TYPE_AUTHOR, VER_DEFAULT, body)
        _, rb = s.recv()
    finally:
        s.close()
    if len(rb) < 6:
        raise TacacsError("short authorization reply")
    status, arg_cnt, msg_len, data_len = struct.unpack("!BBHH", rb[:6])
    lens = rb[6:6 + arg_cnt]
    pos = 6 + arg_cnt + msg_len + data_len
    attrs = {}
    for ln in lens:
        a = rb[pos:pos + ln].decode(errors="replace")
        pos += ln
        for sep in ("=", "*"):
            if sep in a:
                k, v = a.split(sep, 1)
                attrs[k.strip().lower()] = v.strip()
                break
    return status in (AUTHOR_PASS_ADD, AUTHOR_PASS_REPL), attrs


def login(cfg: dict, user: str, password: str, rem_addr: str = ""):
    """Try each configured server in order.

    Returns {"ok": bool, "server": host, "message": str, "attrs": {...}}.
    Raises Unreachable only if *no* server answered.
    """
    errors = []
    for srv in cfg.get("servers", []):
        host, _, port = str(srv).partition(":")
        port = int(port or 49)
        try:
            ok, msg = authenticate(host, port, cfg.get("key", ""), user, password,
                                   timeout=float(cfg.get("timeout", 5)), mode=cfg.get("mode", "pap"),
                                   rem_addr=rem_addr)
        except Unreachable as e:
            errors.append(str(e))
            continue
        attrs = {}
        if ok and cfg.get("authorize", True):
            try:
                _, attrs = authorize(host, port, cfg.get("key", ""), user, cfg.get("service", "shell"),
                                     timeout=float(cfg.get("timeout", 5)), mode=cfg.get("mode", "pap"),
                                     rem_addr=rem_addr)
            except TacacsError as e:
                attrs = {"_author_error": str(e)}
        return {"ok": ok, "server": host, "message": msg, "attrs": attrs}
    raise Unreachable("; ".join(errors) or "no TACACS+ servers configured")


def role_for(cfg: dict, attrs: dict) -> str:
    """Map authorization attributes to a SwitchDeck role."""
    explicit = (attrs.get("switchdeck-role") or "").lower()
    if explicit in ("admin", "operator", "viewer"):
        return explicit
    try:
        lvl = int(attrs.get("priv-lvl", ""))
    except ValueError:
        return cfg.get("default_role", "viewer")
    if lvl >= int(cfg.get("admin_level", 15)):
        return "admin"
    if lvl >= int(cfg.get("operator_level", 7)):
        return "operator"
    return "viewer"
