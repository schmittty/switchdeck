"""HTTP server: JSON API + static UI (stdlib only), plus a plain-HTTP firmware file server."""
import json
import mimetypes
import os
import re
import secrets
import time
import traceback
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import config, db, security
from .drivers import FAMILIES, DriverError, family_for_model
from .drivers.sispm import RESTORABLE
from .services import discovery, jobs, manager

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
ROUTES = []
FW_PORT = int(os.environ.get("SD_FW_PORT", "8081"))
SYSLOG_PUBLIC_PORT = int(os.environ.get("SD_SYSLOG_PUBLIC_PORT", str(config.SYSLOG_PORT)))


class Raw:
    def __init__(self, data, ctype, headers=None):
        self.data, self.ctype, self.headers = data, ctype, headers or {}


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def route(method, pattern, auth=True, role=None):
    """role: minimum role required. Default: GET -> viewer, anything else -> admin."""
    def deco(fn):
        need = role or ("viewer" if method == "GET" else "admin")
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, auth, need))
        return fn
    return deco


# ============================================================ helpers
def public_host(req):
    h = db.setting("public_host") or config.PUBLIC_HOST
    if h:
        return h
    return (req.headers.get("Host") or "127.0.0.1").rsplit(":", 1)[0].strip("[]")


def device_summary(d):
    snap = db.q1("SELECT ts, data FROM snapshots WHERE device_id=?", (d["id"],))
    out = {k: v for k, v in d.items() if k != "secret"}
    out["site"] = (db.q1("SELECT name FROM sites WHERE id=?", (d["site_id"],)) or {}).get("name")
    if d.get("credential_id"):
        out["credential"] = (db.q1("SELECT name FROM credentials WHERE id=?", (d["credential_id"],)) or {}).get("name")
    if snap:
        s = json.loads(snap["data"])
        ports = s.get("ports", [])
        out.update({
            "snapshot_ts": snap["ts"],
            "ports_total": len(ports),
            "ports_up": sum(1 for p in ports if p.get("link_up")),
            "poe": s.get("poe"),
            "cpu": s["system"].get("cpu"), "temp": s["system"].get("temp"),
            "uptime": s["system"].get("uptime"),
            "rx_bps": s.get("rx_bps", 0), "tx_bps": s.get("tx_bps", 0),
            "clients": s.get("mac_count", 0),
            "port_strip": [{"id": p["id"], "up": p.get("link_up"), "speed": p.get("speed"),
                            "admin": p.get("admin_up", True), "media": p.get("media"),
                            "poe": bool((p.get("poe") or {}).get("delivering"))} for p in ports],
        })
    out["capabilities"] = sorted(FAMILIES[d["family"]]["cls"].capabilities) if d["family"] in FAMILIES else []
    return out


def require(body, *keys):
    missing = [k for k in keys if body.get(k) in (None, "")]
    if missing:
        raise HttpError(400, f"missing: {', '.join(missing)}")


def action_job(kind, device_id, user, fn, detail=""):
    d = manager.device(device_id)
    db.audit(user, kind, d["name"], detail)

    def work(job):
        r = manager.run(device_id, fn)
        db.event(device_id, "info", f"{d['name']}: {kind} {detail} — {r if isinstance(r, str) else 'ok'}".strip(), user)
        return r
    return jobs.submit(kind, d["name"], work)


# ============================================================ auth
@route("POST", r"/api/auth/login", auth=False)
def login(req, body, user):
    ip = req.client_address[0]
    try:
        token, u, method = security.login(body.get("username", ""), body.get("password", ""), ip)
    except security.LoginError as e:
        db.audit(body.get("username", "?"), "login_failed", "", f"{ip}: {e}")
        time.sleep(1)
        raise HttpError(401, str(e))
    db.audit(u["username"], "login", "", f"{ip} via {method}")
    req.set_cookie = f"sd_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={security.SESSION_TTL}"
    return {"user": u["username"], "role": u["role"], "must_change": bool(u["must_change"])}


@route("POST", r"/api/auth/logout", role="viewer")
def logout(req, body, user):
    security.logout(req.session_token)
    req.set_cookie = "sd_session=; Path=/; Max-Age=0"
    return {}


@route("GET", r"/api/auth/me")
def me(req, body, user):
    u = req.user_row or {}
    return {"user": user, "role": u.get("role"), "source": u.get("source"),
            "must_change": bool(u.get("must_change")), "demo": config.DEMO,
            "tacacs": bool(security.tacacs_config()["enabled"])}


@route("POST", r"/api/auth/password", role="viewer")
def change_password(req, body, user):
    require(body, "current", "new")
    u = db.q1("SELECT * FROM users WHERE username=?", (user,))
    if u["source"] != "local":
        raise HttpError(400, "TACACS+ users change their password on the TACACS+ server")
    if not security.check_password(body["current"], u["pwhash"]):
        raise HttpError(403, "current password is wrong")
    if body["new"] == body["current"]:
        raise HttpError(400, "choose a different password")
    try:
        security.password_policy(body["new"])
    except security.LoginError as e:
        raise HttpError(400, str(e))
    db.x("UPDATE users SET pwhash=?, must_change=0 WHERE username=?", (security.hash_password(body["new"]), user))
    security.end_user_sessions(user, keep_token=req.session_token)
    db.audit(user, "change_password")
    return {}


# ============================================================ users & authentication (admin)
def _admins_left(excluding):
    return db.q1("SELECT COUNT(*) n FROM users WHERE role='admin' AND source='local' AND disabled=0 AND username<>?",
                 (excluding,))["n"]


@route("GET", r"/api/users", role="admin")
def list_users(req, body, user):
    return db.q("SELECT username, full_name, role, source, must_change, disabled, last_login, created FROM users "
                "ORDER BY source, username")


@route("POST", r"/api/users")
def add_user(req, body, user):
    require(body, "username", "password")
    name = body["username"].strip()
    if not re.match(r"^[\w.@\-]{1,64}$", name):
        raise HttpError(400, "username: letters, digits, . _ - @ only")
    role = body.get("role", "viewer")
    if role not in security.ROLES:
        raise HttpError(400, "role must be admin, operator or viewer")
    if db.q1("SELECT 1 FROM users WHERE username=?", (name,)):
        raise HttpError(409, "that username exists")
    try:
        security.password_policy(body["password"])
    except security.LoginError as e:
        raise HttpError(400, str(e))
    db.x("INSERT INTO users(username, full_name, pwhash, role, source, must_change, created) VALUES (?,?,?,?,'local',?,?)",
         (name, body.get("full_name", ""), security.hash_password(body["password"]), role,
          1 if body.get("must_change", True) else 0, time.time()))
    db.audit(user, "add_user", name, {"role": role})
    return {}


@route("PATCH", r"/api/users/([^/]+)")
def edit_user(req, body, user, name):
    name = unquote(name)
    u = db.q1("SELECT * FROM users WHERE username=?", (name,))
    if not u:
        raise HttpError(404, "no such user")
    demoting = ("role" in body and body["role"] != "admin") or body.get("disabled")
    if u["role"] == "admin" and u["source"] == "local" and demoting and not _admins_left(name):
        raise HttpError(400, "keep at least one enabled local admin (break-glass access)")
    if name == user and body.get("disabled"):
        raise HttpError(400, "you can't disable yourself")
    if "role" in body:
        if body["role"] not in security.ROLES:
            raise HttpError(400, "bad role")
        if u["source"] == "tacacs":
            raise HttpError(400, "TACACS+ users get their role from the TACACS+ server at each login")
        db.x("UPDATE users SET role=? WHERE username=?", (body["role"], name))
    if "disabled" in body:
        db.x("UPDATE users SET disabled=? WHERE username=?", (1 if body["disabled"] else 0, name))
        if body["disabled"]:
            security.end_user_sessions(name)
    if "full_name" in body:
        db.x("UPDATE users SET full_name=? WHERE username=?", (body["full_name"][:100], name))
    if body.get("password"):
        if u["source"] != "local":
            raise HttpError(400, "TACACS+ passwords are managed on the TACACS+ server")
        try:
            security.password_policy(body["password"])
        except security.LoginError as e:
            raise HttpError(400, str(e))
        db.x("UPDATE users SET pwhash=?, must_change=1 WHERE username=?", (security.hash_password(body["password"]), name))
        security.end_user_sessions(name)
    detail = {k: v for k, v in body.items() if k != "password"}
    if body.get("password"):
        detail["password"] = "reset"
    db.audit(user, "edit_user", name, detail)
    return {}


@route("DELETE", r"/api/users/([^/]+)")
def delete_user(req, body, user, name):
    name = unquote(name)
    u = db.q1("SELECT * FROM users WHERE username=?", (name,))
    if not u:
        raise HttpError(404, "no such user")
    if name == user:
        raise HttpError(400, "you can't delete yourself")
    if u["role"] == "admin" and u["source"] == "local" and not _admins_left(name):
        raise HttpError(400, "keep at least one enabled local admin (break-glass access)")
    db.x("DELETE FROM sessions WHERE username=?", (name,))
    db.x("DELETE FROM users WHERE username=?", (name,))
    db.audit(user, "delete_user", name)
    return {}


def _server_list(v):
    if isinstance(v, str):
        v = v.replace("\n", ",").split(",")
    return [x.strip() for x in v if x and x.strip()]


@route("GET", r"/api/auth/tacacs", role="admin")
def get_tacacs(req, body, user):
    cfg = security.tacacs_config()
    cfg["key_set"] = bool(cfg.pop("key", ""))
    return cfg


@route("PUT", r"/api/auth/tacacs")
def put_tacacs(req, body, user):
    cfg = security.tacacs_config()
    for k in ("enabled", "servers", "mode", "timeout", "authorize", "service", "admin_level",
              "operator_level", "default_role", "local_fallback"):
        if k in body:
            cfg[k] = body[k]
    if body.get("key"):
        cfg["key"] = body["key"]
    cfg["servers"] = _server_list(cfg["servers"])
    if cfg["mode"] not in ("pap", "ascii") or cfg["default_role"] not in security.ROLES \
            or cfg["local_fallback"] not in ("always", "unreachable"):
        raise HttpError(400, "invalid mode, default_role or local_fallback")
    if cfg["enabled"] and (not cfg["servers"] or not cfg["key"]):
        raise HttpError(400, "add at least one server and the shared key before enabling")
    db.set_setting("tacacs", cfg)
    db.audit(user, "tacacs_settings", "", {k: v for k, v in cfg.items() if k != "key"})
    return get_tacacs(req, body, user)


@route("POST", r"/api/auth/tacacs/test")
def test_tacacs(req, body, user):
    from . import tacacs
    require(body, "username", "password")
    cfg = security.tacacs_config()
    for k in ("mode", "service", "admin_level", "operator_level", "default_role", "timeout"):
        if body.get(k) not in (None, ""):
            cfg[k] = body[k]
    if body.get("key"):
        cfg["key"] = body["key"]
    if body.get("servers"):
        cfg["servers"] = _server_list(body["servers"])
    db.audit(user, "tacacs_test", body["username"])
    try:
        r = tacacs.login(cfg, body["username"], body["password"], rem_addr=req.client_address[0])
    except tacacs.Unreachable as e:
        return {"ok": False, "result": "unreachable", "detail": str(e)}
    except tacacs.TacacsError as e:
        return {"ok": False, "result": "error", "detail": str(e)}
    return {"ok": r["ok"], "result": "accepted" if r["ok"] else "rejected", "server": r["server"],
            "message": r["message"], "attributes": r["attrs"],
            "role": tacacs.role_for(cfg, r["attrs"]) if r["ok"] else None}


# ============================================================ switch credential profiles (admin)
@route("GET", r"/api/credentials", role="admin")
def list_credentials(req, body, user):
    return db.q("SELECT c.id, c.name, c.username, c.notes, c.created, "
                "(SELECT COUNT(*) FROM devices d WHERE d.credential_id=c.id) AS devices FROM credentials c ORDER BY name")


@route("POST", r"/api/credentials")
def add_credential(req, body, user):
    require(body, "name", "username", "password")
    if db.q1("SELECT 1 FROM credentials WHERE name=?", (body["name"],)):
        raise HttpError(409, "a profile with that name exists")
    cid = db.x("INSERT INTO credentials(name, username, secret, notes, created) VALUES (?,?,?,?,?)",
               (body["name"].strip(), body["username"], security.encrypt(body["password"]), body.get("notes", ""), time.time()))
    db.audit(user, "add_credential", body["name"])
    return {"id": cid}


@route("PATCH", r"/api/credentials/(\d+)")
def edit_credential(req, body, user, cid):
    cid = int(cid)
    c = db.q1("SELECT * FROM credentials WHERE id=?", (cid,))
    if not c:
        raise HttpError(404, "no such profile")
    for k in ("name", "username", "notes"):
        if k in body:
            db.x(f"UPDATE credentials SET {k}=? WHERE id=?", (body[k], cid))
    if body.get("password"):
        db.x("UPDATE credentials SET secret=? WHERE id=?", (security.encrypt(body["password"]), cid))
    for d in db.q("SELECT id FROM devices WHERE credential_id=?", (cid,)):
        manager.forget(d["id"])
    db.audit(user, "edit_credential", c["name"], {k: v for k, v in body.items() if k != "password"})
    return {}


@route("DELETE", r"/api/credentials/(\d+)")
def delete_credential(req, body, user, cid):
    n = db.q1("SELECT COUNT(*) n FROM devices WHERE credential_id=?", (int(cid),))["n"]
    if n:
        raise HttpError(400, f"{n} switch(es) still use this profile")
    db.x("DELETE FROM credentials WHERE id=?", (int(cid),))
    db.audit(user, "delete_credential", cid)
    return {}


# ============================================================ overview & meta
@route("GET", r"/api/overview")
def overview(req, body, user):
    devs = [device_summary(d) for d in db.q("SELECT * FROM devices ORDER BY name")]
    poe_used = sum((d.get("poe") or {}).get("used_w") or 0 for d in devs)
    poe_budget = sum((d.get("poe") or {}).get("budget_w") or 0 for d in devs)
    return {
        "devices": {"total": len(devs), "online": sum(d["status"] == "online" for d in devs),
                    "offline": sum(d["status"] == "offline" for d in devs),
                    "pending": sum(d["status"] == "pending" for d in devs)},
        "ports": {"total": sum(d.get("ports_total", 0) for d in devs), "up": sum(d.get("ports_up", 0) for d in devs)},
        "poe": {"used_w": round(poe_used, 1), "budget_w": round(poe_budget, 1)},
        "throughput": {"rx_bps": sum(d.get("rx_bps", 0) for d in devs), "tx_bps": sum(d.get("tx_bps", 0) for d in devs)},
        "clients": sum(d.get("clients", 0) for d in devs),
        "firmware": _firmware_compliance(devs),
        "events": db.q("SELECT e.*, d.name AS device FROM events e LEFT JOIN devices d ON d.id=e.device_id "
                       "ORDER BY e.ts DESC LIMIT 12"),
        "discovered": db.q("SELECT COUNT(*) AS n FROM discovered")[0]["n"],
        "list": devs,
    }


def _firmware_compliance(devs):
    by_model = {}
    for d in devs:
        if d.get("model") and d.get("firmware"):
            by_model.setdefault(d["model"], set()).add(d["firmware"])
    return [{"model": m, "versions": sorted(v), "consistent": len(v) == 1} for m, v in sorted(by_model.items())]


@route("GET", r"/api/families")
def families(req, body, user):
    return [{"id": k, "label": v["label"], "default_port": v["default_port"], "models": v["models"],
             "capabilities": sorted(v["cls"].capabilities)} for k, v in FAMILIES.items()]


@route("GET", r"/api/sites")
def sites(req, body, user):
    return db.q("SELECT s.*, (SELECT COUNT(*) FROM devices d WHERE d.site_id=s.id) AS devices FROM sites s ORDER BY name")


@route("POST", r"/api/sites")
def add_site(req, body, user):
    require(body, "name")
    sid = db.x("INSERT INTO sites(name, notes) VALUES (?,?)", (body["name"].strip(), body.get("notes", "")))
    db.audit(user, "add_site", body["name"])
    return {"id": sid}


@route("GET", r"/api/settings")
def get_settings(req, body, user):
    return {"ntp_servers": db.setting("ntp_servers", ["pool.ntp.org"]),
            "public_host": db.setting("public_host", config.PUBLIC_HOST),
            "syslog_port": SYSLOG_PUBLIC_PORT, "firmware_port": FW_PORT,
            "backup_retention": db.setting("backup_retention", 60),
            "poll_interval": config.POLL_INTERVAL, "backup_interval": config.BACKUP_INTERVAL,
            "detected_host": public_host(req)}


@route("PUT", r"/api/settings")
def put_settings(req, body, user):
    for k in ("ntp_servers", "public_host", "backup_retention"):
        if k in body:
            db.set_setting(k, body[k])
    db.audit(user, "settings", "", body)
    return get_settings(req, body, user)


# ============================================================ devices
@route("GET", r"/api/devices")
def list_devices(req, body, user):
    return [device_summary(d) for d in db.q("SELECT * FROM devices ORDER BY name")]


@route("POST", r"/api/devices")
def add_device(req, body, user):
    require(body, "host")
    cred = int(body["credential_id"]) if body.get("credential_id") else None
    if cred:
        if not db.q1("SELECT 1 FROM credentials WHERE id=?", (cred,)):
            raise HttpError(400, "no such credential profile")
    else:
        require(body, "username", "password")
    fam = body.get("family") or family_for_model(body.get("model", ""))
    if fam not in FAMILIES:
        raise HttpError(400, "family must be sispm or lss")
    if db.q1("SELECT 1 FROM devices WHERE host=?", (body["host"],)):
        raise HttpError(409, f"{body['host']} is already managed")
    did = db.x("INSERT INTO devices(site_id, name, host, port, scheme, family, username, secret, credential_id, created) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)",
               (body.get("site_id") or 1, body.get("name") or body["host"], body["host"],
                int(body.get("port") or FAMILIES[fam]["default_port"]),
                body.get("scheme") or ("https" if fam == "lss" else "http"), fam,
                body.get("username") or "", security.encrypt(body.get("password") or ""), cred, time.time()))
    db.x("DELETE FROM discovered WHERE host=?", (body["host"],))
    db.audit(user, "add_device", body["host"], {"family": fam})
    st = manager.poll(did)
    d = manager.device(did)
    return {"id": did, "status": d["status"], "error": d["last_error"], "model": (st or {}).get("system", {}).get("model")}


@route("GET", r"/api/devices/(\d+)")
def get_device(req, body, user, did):
    d = manager.device(int(did))
    out = device_summary(d)
    snap = db.q1("SELECT data FROM snapshots WHERE device_id=?", (d["id"],))
    out["state"] = json.loads(snap["data"]) if snap else None
    if out["state"]:
        roles = {(r["device_id"], r["port"]): r["role"] for r in db.q("SELECT * FROM port_roles WHERE device_id=?", (d["id"],))}
        nb = {n.get("port"): n for n in out["state"].get("neighbors", [])}
        for p in out["state"]["ports"]:
            p["role"] = port_role(d["id"], p, roles, nb)
            p["role_manual"] = (d["id"], p["id"]) in roles
            p["neighbor"] = nb.get(p["id"])
    out["backups"] = db.q("SELECT id, ts, sha, trigger, note FROM backups WHERE device_id=? ORDER BY ts DESC LIMIT 50", (d["id"],))
    out["events"] = db.q("SELECT * FROM events WHERE device_id=? ORDER BY ts DESC LIMIT 50", (d["id"],))
    return out


@route("PATCH", r"/api/devices/(\d+)")
def patch_device(req, body, user, did):
    did = int(did)
    d = manager.device(did)
    fields, args = [], []
    if "credential_id" in body:
        body["credential_id"] = int(body["credential_id"]) if body["credential_id"] else None
    for k in ("name", "host", "port", "scheme", "username", "site_id", "credential_id"):
        if k in body:
            fields.append(f"{k}=?")
            args.append(body[k])
    if body.get("password"):
        fields.append("secret=?")
        args.append(security.encrypt(body["password"]))
    if fields:
        db.x(f"UPDATE devices SET {', '.join(fields)} WHERE id=?", (*args, did))
        manager.forget(did)
    db.audit(user, "edit_device", d["name"], {k: v for k, v in body.items() if k != "password"})
    return device_summary(manager.device(did))


@route("DELETE", r"/api/devices/(\d+)")
def delete_device(req, body, user, did):
    d = manager.device(int(did))
    manager.forget(d["id"])
    db.x("DELETE FROM devices WHERE id=?", (d["id"],))
    db.audit(user, "remove_device", d["name"], d["host"])
    return {}


@route("POST", r"/api/devices/(\d+)/poll", role="operator")
def poll_device(req, body, user, did):
    st = manager.poll(int(did))
    d = manager.device(int(did))
    if st is None:
        raise HttpError(502, d["last_error"] or "poll failed")
    return {"status": d["status"]}


@route("POST", r"/api/devices/(\d+)/adopt")
def adopt(req, body, user, did):
    did = int(did)
    host = public_host(req)
    ntp = db.setting("ntp_servers", ["pool.ntp.org"])
    d = manager.device(did)
    db.audit(user, "adopt", d["name"], {"syslog": host, "ntp": ntp})

    def work(job):
        steps = []
        def step(label, fn):
            job["progress"] = label
            try:
                manager.run(did, fn)
                steps.append(f"✓ {label}")
            except DriverError as e:
                steps.append(f"✗ {label}: {e}")
        if body.get("name"):
            step("set system name", lambda drv: drv.set_identity(name=body["name"]))
        step(f"syslog → {host}:{SYSLOG_PUBLIC_PORT}", lambda drv: drv.set_syslog(host, SYSLOG_PUBLIC_PORT))
        step(f"NTP → {', '.join(ntp)}", lambda drv: drv.set_ntp(ntp))
        step("save startup-config", lambda drv: drv.save())
        job["progress"] = "baseline backup"
        try:
            manager.backup(did, "adopt", "baseline at adoption")
            steps.append("✓ baseline backup")
        except Exception as e:  # noqa: BLE001
            steps.append(f"✗ baseline backup: {e}")
        db.x("UPDATE devices SET adopted=1 WHERE id=?", (did,))
        db.event(did, "info", f"{d['name']} adopted: " + "; ".join(steps), user)
        return steps
    return jobs.submit("adopt", d["name"], work)


@route("POST", r"/api/devices/(\d+)/ports/(\d+)", role="operator")
def set_port(req, body, user, did, port):
    did, port = int(did), int(port)
    manager.run(did, lambda drv: drv.set_port(port, enabled=body.get("enabled"), description=body.get("description")))
    if body.get("save"):
        manager.run(did, lambda drv: drv.save())
    d = manager.device(did)
    db.audit(user, "set_port", f"{d['name']}:{port}", body)
    db.event(did, "info", f"{d['name']} port {port} updated {json.dumps(body)}", user)
    manager.poll(did)
    return {}


@route("POST", r"/api/devices/(\d+)/ports/(\d+)/poe-cycle", role="operator")
def poe_cycle(req, body, user, did, port):
    port = int(port)
    secs = max(2, min(int(body.get("seconds", 5)), 60))
    return action_job("poe_cycle", int(did), user, lambda drv: drv.poe_cycle(port, secs), f"port {port}")


@route("POST", r"/api/devices/(\d+)/ports/(\d+)/cable-test", role="operator")
def cable_test(req, body, user, did, port):
    port = int(port)
    return action_job("cable_test", int(did), user, lambda drv: drv.cable_test(port), f"port {port}")


@route("POST", r"/api/devices/(\d+)/reboot")
def reboot(req, body, user, did):
    return action_job("reboot", int(did), user, lambda drv: drv.reboot())


@route("POST", r"/api/devices/(\d+)/save", role="operator")
def save(req, body, user, did):
    return action_job("save_config", int(did), user, lambda drv: drv.save())


@route("POST", r"/api/devices/(\d+)/identity")
def identity(req, body, user, did):
    did = int(did)
    manager.run(did, lambda drv: drv.set_identity(body.get("name"), body.get("location"), body.get("contact")))
    db.audit(user, "set_identity", manager.device(did)["name"], body)
    manager.poll(did)
    return {}


@route("GET", r"/api/devices/(\d+)/metrics")
def metrics(req, body, user, did):
    hours = float(req.query.get("hours", ["24"])[0])
    return db.q("SELECT ts, cpu, temp, poe_w, rx_bps, tx_bps FROM metrics WHERE device_id=? AND ts>? ORDER BY ts",
                (int(did), time.time() - hours * 3600))


@route("POST", r"/api/devices/(\d+)/raw")
def raw(req, body, user, did):
    did = int(did)
    require(body, "path")
    method = body.get("method", "GET").upper()
    if method != "GET" and not body.get("confirm"):
        raise HttpError(400, "write calls need confirm=true")
    db.audit(user, "api_explorer", manager.device(did)["name"], {"method": method, "path": body["path"]})
    return {"response": manager.run(did, lambda drv: drv.raw(method, body["path"], body.get("body")))}


@route("GET", r"/api/devices/(\d+)/endpoints", role="admin")
def endpoints(req, body, user, did):
    did = int(did)
    d = manager.device(did)
    if d["family"] == "lss":
        return {"get": manager.run(did, lambda drv: drv.endpoints("get")),
                "post": manager.run(did, lambda drv: drv.endpoints("post"))}
    from .drivers.sispm import SECTIONS
    return {"get": [v[0] for v in SECTIONS.values()] + ["/api/get_poe_status", "/api/get_port_statistics",
                    "/api/get_dynamic_mac_table", "/api/dev_list_table", "/api/get_sfp_port_detail",
                    "/api/get_syslog", "/api/get_di_do_status", "/api/get_firmware_upgrade_status"],
            "post": [v[1] for v in SECTIONS.values() if v[1]]}


# ============================================================ backups
@route("POST", r"/api/devices/(\d+)/backups", role="operator")
def backup_now(req, body, user, did):
    did = int(did)
    d = manager.device(did)
    db.audit(user, "backup", d["name"])
    return jobs.submit("backup", d["name"], lambda job: manager.backup(did, "manual", body.get("note", "")))


@route("POST", r"/api/backups/run-all", role="operator")
def backup_all(req, body, user):
    db.audit(user, "backup_all")
    return jobs.submit("backup_all", "all devices", lambda job: manager.backup_all("manual"))


@route("GET", r"/api/backups")
def list_backups(req, body, user):
    return db.q("SELECT b.id, b.device_id, b.ts, b.sha, b.trigger, b.note, d.name AS device, d.model "
                "FROM backups b JOIN devices d ON d.id=b.device_id ORDER BY b.ts DESC LIMIT 300")


@route("GET", r"/api/backups/(\d+)")
def get_backup(req, body, user, bid):
    b = db.q1("SELECT b.*, d.name AS device FROM backups b JOIN devices d ON d.id=b.device_id WHERE b.id=?", (int(bid),))
    if not b:
        raise HttpError(404, "no such backup")
    return b


@route("GET", r"/api/backups/(\d+)/diff")
def diff_backup(req, body, user, bid):
    b = get_backup(req, body, user, bid)
    against = req.query.get("against", ["prev"])[0]
    if against == "prev":
        o = db.q1("SELECT * FROM backups WHERE device_id=? AND ts<? ORDER BY ts DESC LIMIT 1", (b["device_id"], b["ts"]))
    else:
        o = db.q1("SELECT * FROM backups WHERE id=?", (int(against),))
    if not o:
        return {"diff": "", "note": "no earlier backup to compare"}
    a_label = time.strftime("%Y-%m-%d %H:%M", time.localtime(o["ts"]))
    b_label = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["ts"]))
    return {"diff": manager.diff(o["content"], b["content"], a_label, b_label), "against": o["id"]}


@route("GET", r"/api/backups/(\d+)/download")
def download_backup(req, body, user, bid):
    b = get_backup(req, body, user, bid)
    name = re.sub(r"[^\w.-]+", "_", f"{b['device']}_{time.strftime('%Y%m%d-%H%M', time.localtime(b['ts']))}.json")
    return Raw(b["content"].encode(), "application/json", {"Content-Disposition": f'attachment; filename="{name}"'})


@route("POST", r"/api/backups/(\d+)/note", role="operator")
def note_backup(req, body, user, bid):
    db.x("UPDATE backups SET note=? WHERE id=?", (body.get("note", "")[:200], int(bid)))
    return {}


@route("POST", r"/api/backups/(\d+)/restore")
def restore(req, body, user, bid):
    b = get_backup(req, body, user, bid)
    did = int(body.get("device_id") or b["device_id"])
    snap = json.loads(b["content"])
    sections = body.get("sections") or list(RESTORABLE)
    if body.get("include_ip"):
        sections.append("ip")
    d = manager.device(did)
    db.audit(user, "restore", d["name"], {"backup": b["id"], "sections": sections})

    def work(job):
        job["progress"] = "safety backup of current config"
        manager.backup(did, "pre-restore", f"automatic before restoring #{b['id']}")
        job["progress"] = "applying"
        done = manager.run(did, lambda drv: drv.restore(snap, sections))
        db.event(did, "warning", f"{d['name']} restored from backup #{b['id']} ({', '.join(done)})", user)
        return done
    return jobs.submit("restore", d["name"], work)


# ============================================================ events / audit / jobs
@route("GET", r"/api/events")
def events(req, body, user):
    limit = min(int(req.query.get("limit", ["200"])[0]), 1000)
    dev = req.query.get("device", [None])[0]
    if dev:
        return db.q("SELECT e.*, d.name AS device FROM events e LEFT JOIN devices d ON d.id=e.device_id "
                    "WHERE e.device_id=? ORDER BY e.ts DESC LIMIT ?", (int(dev), limit))
    return db.q("SELECT e.*, d.name AS device FROM events e LEFT JOIN devices d ON d.id=e.device_id "
                "ORDER BY e.ts DESC LIMIT ?", (limit,))


@route("GET", r"/api/audit", role="admin")
def audit(req, body, user):
    return db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT 300")


@route("GET", r"/api/jobs")
def list_jobs(req, body, user):
    return jobs.recent()


@route("GET", r"/api/jobs/(\d+)")
def get_job(req, body, user, jid):
    j = jobs.get(int(jid))
    if not j:
        raise HttpError(404, "no such job")
    return j


# ============================================================ discovery
@route("POST", r"/api/discovery")
def discover(req, body, user):
    require(body, "cidr")
    db.audit(user, "discovery", body["cidr"])
    return jobs.submit("discovery", body["cidr"], discovery.scan, body["cidr"])


@route("GET", r"/api/discovery")
def discovered(req, body, user):
    return db.q("SELECT * FROM discovered ORDER BY host")


@route("DELETE", r"/api/discovery/(.+)")
def dismiss(req, body, user, host):
    db.x("DELETE FROM discovered WHERE host=?", (unquote(host),))
    return {}


# ============================================================ firmware
@route("GET", r"/api/firmware")
def firmware_list(req, body, user):
    rows = db.q("SELECT * FROM firmware ORDER BY uploaded DESC")
    for r in rows:
        r["models"] = json.loads(r["models"] or "[]")
    return rows


@route("POST", r"/api/firmware")
def firmware_upload(req, body, user):
    name = os.path.basename(unquote(req.headers.get("X-Filename", "")))
    if not name or not re.match(r"^[\w.\-+]+$", name):
        raise HttpError(400, "X-Filename header with a plain file name is required")
    data = req.raw_body
    if not data:
        raise HttpError(400, "empty upload")
    token = secrets.token_urlsafe(12)
    folder = os.path.join(config.FIRMWARE_DIR, token)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)
    models = [m.strip() for m in unquote(req.headers.get("X-Models", "")).split(",") if m.strip()]
    fid = db.x("INSERT INTO firmware(filename, family, models, version, size, uploaded) VALUES (?,?,?,?,?,?)",
               (f"{token}/{name}", req.headers.get("X-Family", "sispm"), json.dumps(models),
                unquote(req.headers.get("X-Version", "")), len(data), time.time()))
    db.audit(user, "firmware_upload", name, {"size": len(data), "models": models})
    return {"id": fid}


@route("DELETE", r"/api/firmware/(\d+)")
def firmware_delete(req, body, user, fid):
    f = db.q1("SELECT * FROM firmware WHERE id=?", (int(fid),))
    if f:
        path = os.path.join(config.FIRMWARE_DIR, f["filename"])
        if os.path.exists(path):
            os.remove(path)
        db.x("DELETE FROM firmware WHERE id=?", (f["id"],))
        db.audit(user, "firmware_delete", f["filename"])
    return {}


@route("POST", r"/api/firmware/(\d+)/push")
def firmware_push(req, body, user, fid):
    f = db.q1("SELECT * FROM firmware WHERE id=?", (int(fid),))
    if not f:
        raise HttpError(404, "no such firmware")
    ids = [int(i) for i in body.get("device_ids", [])]
    if not ids:
        raise HttpError(400, "select at least one device")
    url = f"http://{public_host(req)}:{FW_PORT}/fw/{f['filename']}"
    db.audit(user, "firmware_push", f["filename"], {"devices": ids, "url": url})

    def work(job):
        results = {}
        for did in ids:   # sequential on purpose: one switch at a time
            d = manager.device(did)
            job["progress"] = f"{d['name']}: sending"
            try:
                manager.backup(did, "pre-upgrade", f"before firmware {f['version'] or f['filename']}")
                manager.run(did, lambda drv: drv.firmware_upgrade(url))
                state = "requested"
                for i in range(120):  # up to ~10 minutes
                    time.sleep(5)
                    try:
                        state = manager.run(did, lambda drv: drv.firmware_status())
                    except DriverError:
                        state = "rebooting"
                        manager.forget(did)
                    job["progress"] = f"{d['name']}: {state}"
                    if state.lower() in ("idle", "done", "success", "finished") and i > 2:
                        break
                results[d["name"]] = state
                db.event(did, "info", f"{d['name']} firmware push: {state}", user)
            except Exception as e:  # noqa: BLE001
                results[d["name"]] = f"failed: {e}"
                db.event(did, "error", f"{d['name']} firmware push failed: {e}", user)
        return results
    return jobs.submit("firmware_push", f["filename"], work)


# ============================================================ integrations (dashboards / Home Assistant)
def port_role(device_id, port, roles, neighbors_by_port):
    """uplink | access. Manual override wins; otherwise SFP/fiber ports, ports described as
    uplink/trunk, and ports whose neighbor is another switch count as uplinks."""
    r = roles.get((device_id, port["id"]))
    if r in ("uplink", "access"):
        return r
    desc = (port.get("description") or "").lower()
    if port.get("media") == "fiber" or "uplink" in desc or "trunk" in desc:
        return "uplink"
    if (neighbors_by_port.get(port["id"]) or {}).get("type", "").upper() == "SWITCH":
        return "uplink"
    return "access"


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "unknown").lower()).strip("_")


def exec_rollup():
    now = time.time()
    devs = db.q("SELECT d.*, s.name AS site FROM devices d LEFT JOIN sites s ON s.id=d.site_id ORDER BY d.name")
    roles = {(r["device_id"], r["port"]): r["role"] for r in db.q("SELECT * FROM port_roles")}
    last_bk = {r["device_id"]: r["ts"] for r in db.q("SELECT device_id, MAX(ts) AS ts FROM backups GROUP BY device_id")}
    out = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "devices_total": len(devs), "devices_online": 0, "devices_offline": 0, "devices_pending": 0,
        "ports_total": 0, "ports_active": 0,
        "access_ports_total": 0, "access_ports_active": 0,
        "uplinks_total": 0, "uplinks_up": 0, "uplinks_down": 0,
        "poe_used_w": 0.0, "poe_budget_w": 0.0, "poe_pct": 0, "poe_ports_powered": 0,
        "cameras": 0, "clients": 0, "rx_mbps": 0.0, "tx_mbps": 0.0,
        "backups_current": 0, "backups_stale": 0,
        "config_changes_24h": db.q1("SELECT COUNT(*) n FROM events WHERE source='backup' AND level='warning' AND ts>?", (now - 86400,))["n"],
        "alerts_24h": db.q1("SELECT COUNT(*) n FROM events WHERE level IN ('warning','error') AND ts>?", (now - 86400,))["n"],
        "errors_24h": db.q1("SELECT COUNT(*) n FROM events WHERE level='error' AND ts>?", (now - 86400,))["n"],
        "offline_devices": [], "models": {}, "sites": {}, "firmware": {}, "devices": [],
    }
    for d in devs:
        out[f"devices_{d['status']}"] = out.get(f"devices_{d['status']}", 0) + 1
        snap = db.q1("SELECT data FROM snapshots WHERE device_id=?", (d["id"],))
        st = json.loads(snap["data"]) if snap else {"ports": [], "neighbors": []}
        nb = {n.get("port"): n for n in st.get("neighbors", [])}
        dev = {"id": d["id"], "name": d["name"], "site": d["site"], "model": d["model"] or d["family"], "host": d["host"],
               "status": d["status"], "firmware": d["firmware"], "ports_active": 0, "ports_total": 0,
               "access_ports_active": 0, "access_ports_total": 0, "uplinks_up": 0, "uplinks_total": 0,
               "poe_used_w": 0, "poe_budget_w": 0, "cameras": 0,
               "last_seen_min": round((now - d["last_seen"]) / 60, 1) if d["last_seen"] else None,
               "backup_age_h": round((now - last_bk[d["id"]]) / 3600, 1) if d["id"] in last_bk else None}
        if d["status"] != "online":
            out["offline_devices"].append(d["name"])
        online = d["status"] == "online"
        for p in st.get("ports", []):
            role = port_role(d["id"], p, roles, nb)
            up = bool(p.get("link_up")) and online
            dev["ports_total"] += 1
            dev["ports_active"] += up
            # An auto-detected uplink with no link is just an empty SFP cage: count it as spare, not down.
            # Only ports explicitly tagged "uplink" raise uplinks_down when they lose link.
            if role == "uplink" and not up and (d["id"], p["id"]) not in roles:
                dev["spare_ports"] = dev.get("spare_ports", 0) + 1
                continue
            if role == "uplink":
                dev["uplinks_total"] += 1
                dev["uplinks_up"] += up
            else:
                dev["access_ports_total"] += 1
                dev["access_ports_active"] += up
            if online and (p.get("poe") or {}).get("delivering"):
                out["poe_ports_powered"] += 1
        if online:
            poe = st.get("poe") or {}
            dev["poe_used_w"] = round(poe.get("used_w") or 0, 1)
            dev["poe_budget_w"] = round(poe.get("budget_w") or 0, 1)
            dev["cameras"] = sum(1 for n in st.get("neighbors", []) if "camera" in (n.get("type") or "").lower())
            out["clients"] += st.get("mac_count", 0) or 0
            out["rx_mbps"] += (st.get("rx_bps") or 0) / 1e6
            out["tx_mbps"] += (st.get("tx_bps") or 0) / 1e6
        for k in ("ports_total", "ports_active", "access_ports_total", "access_ports_active", "uplinks_total",
                  "uplinks_up", "poe_used_w", "poe_budget_w", "cameras"):
            out[k] += dev[k]
        fresh = d["id"] in last_bk and now - last_bk[d["id"]] < 36 * 3600
        out["backups_current" if fresh else "backups_stale"] += 1
        m = out["models"].setdefault(dev["model"], {"total": 0, "online": 0, "ports_active": 0, "access_ports_active": 0})
        m["total"] += 1
        m["online"] += online
        m["ports_active"] += dev["ports_active"]
        m["access_ports_active"] += dev["access_ports_active"]
        s = out["sites"].setdefault(d["site"] or "Default", {"total": 0, "online": 0})
        s["total"] += 1
        s["online"] += online
        if d["firmware"]:
            out["firmware"].setdefault(dev["model"], set()).add(d["firmware"])
        out["devices"].append(dev)
    out["uplinks_down"] = out["uplinks_total"] - out["uplinks_up"]
    out["poe_used_w"] = round(out["poe_used_w"], 1)
    out["poe_budget_w"] = round(out["poe_budget_w"], 1)
    out["poe_pct"] = round(out["poe_used_w"] / out["poe_budget_w"] * 100) if out["poe_budget_w"] else 0
    out["rx_mbps"], out["tx_mbps"] = round(out["rx_mbps"], 1), round(out["tx_mbps"], 1)
    out["firmware"] = {m: sorted(v) for m, v in out["firmware"].items()}
    out["firmware_mismatch_models"] = sum(1 for v in out["firmware"].values() if len(v) > 1)
    # Flat per-model keys so HA templates stay one-liners: model_sispm1040_582_lrt_online etc.
    for m, v in out["models"].items():
        for k, n in v.items():
            out[f"model_{_slug(m)}_{k}"] = n
    out["health"] = ("down" if out["devices_total"] and out["devices_online"] == 0 else
                     "degraded" if out["devices_offline"] or out["uplinks_down"] or out["errors_24h"] else "ok")
    return out


@route("GET", r"/api/v1/summary", auth="read")
def exec_summary(req, body, user):
    """Fleet roll-up for dashboards (e.g. a Home Assistant RESTful sensor). Read-only."""
    return exec_rollup()


@route("GET", r"/api/v1/exec", auth="read")
def exec_summary_alias(req, body, user):
    return exec_rollup()


@route("GET", r"/api/v1/devices", auth="read")
def exec_devices(req, body, user):
    return exec_rollup()["devices"]


@route("GET", r"/api/v1/events", auth="read")
def exec_events(req, body, user):
    hours = float(req.query.get("hours", ["24"])[0])
    return db.q("SELECT e.ts, e.level, e.source, e.message, d.name AS device FROM events e "
                "LEFT JOIN devices d ON d.id=e.device_id WHERE e.ts>? AND e.level IN ('warning','error') "
                "ORDER BY e.ts DESC LIMIT 100", (time.time() - hours * 3600,))


@route("POST", r"/api/devices/(\d+)/ports/(\d+)/role", role="operator")
def set_port_role(req, body, user, did, port):
    role = body.get("role", "auto")
    if role == "auto":
        db.x("DELETE FROM port_roles WHERE device_id=? AND port=?", (int(did), int(port)))
    elif role in ("uplink", "access"):
        db.x("INSERT INTO port_roles(device_id, port, role) VALUES (?,?,?) "
             "ON CONFLICT(device_id, port) DO UPDATE SET role=excluded.role", (int(did), int(port), role))
    else:
        raise HttpError(400, "role must be uplink, access or auto")
    db.audit(user, "port_role", f"{did}:{port}", role)
    return {}


@route("GET", r"/api/tokens", role="admin")
def list_tokens(req, body, user):
    return db.q("SELECT id, name, prefix, created, last_used, created_by FROM api_tokens ORDER BY created DESC")


@route("POST", r"/api/tokens")
def new_token(req, body, user):
    require(body, "name")
    tok = security.create_api_token(body["name"][:60], user)
    db.audit(user, "create_token", body["name"])
    return {"token": tok}


@route("DELETE", r"/api/tokens/(\d+)")
def revoke_token(req, body, user, tid):
    db.x("DELETE FROM api_tokens WHERE id=?", (int(tid),))
    db.audit(user, "revoke_token", tid)
    return {}


# ============================================================ request handler
class Handler(BaseHTTPRequestHandler):
    server_version = "SwitchDeck/0.1"

    def log_message(self, fmt, *args):
        if os.environ.get("SD_ACCESS_LOG") == "1":
            super().log_message(fmt, *args)

    def _send(self, code, payload, ctype="application/json", extra=None):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "set_cookie", None):
            self.send_header("Set-Cookie", self.set_cookie)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/").removeprefix("static/")
        full = os.path.realpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.realpath(WEB_DIR)) or not os.path.isfile(full):
            full = os.path.join(WEB_DIR, "index.html")  # SPA fallback
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype, {"Cache-Control": "no-cache"})

    def _dispatch(self, method):
        self.set_cookie = None
        url = urlparse(self.path)
        self.query = parse_qs(url.query)
        if not url.path.startswith("/api/"):
            if method in ("GET", "HEAD"):
                return self._static(url.path)
            return self._send(404, {"error": "not found"})
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        self.session_token = c["sd_session"].value if "sd_session" in c else ""
        length = int(self.headers.get("Content-Length") or 0)
        if length > 512 * 1024 * 1024:
            return self._send(413, {"error": "too large"})
        self.raw_body = self.rfile.read(length) if length else b""
        body = {}
        if self.raw_body and (self.headers.get("Content-Type") or "").startswith("application/json"):
            try:
                body = json.loads(self.raw_body)
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
        for m, rx, fn, auth, need in ROUTES:
            if m != method:
                continue
            match = rx.match(url.path)
            if not match:
                continue
            self.user_row = security.session_user(self.session_token) if auth else None
            user, role = (self.user_row["username"], self.user_row["role"]) if self.user_row else (None, None)
            if not user and auth == "read":
                bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                user = security.api_token_name(bearer or self.query.get("token", [""])[0])
                role = "viewer" if user else None
            if auth and not user:
                return self._send(401, {"error": "login required"})
            if auth and self.user_row and self.user_row["must_change"] and url.path not in (
                    "/api/auth/me", "/api/auth/password", "/api/auth/logout"):
                return self._send(403, {"error": "password change required", "must_change": True})
            if auth and security.ROLE_RANK.get(role, -1) < security.ROLE_RANK[need]:
                return self._send(403, {"error": f"requires the {need} role"})
            # CSRF: state-changing calls must come from our own page (custom header can't be set cross-site).
            if method != "GET" and auth and self.headers.get("X-Requested-With") != "switchdeck":
                return self._send(403, {"error": "missing X-Requested-With"})
            try:
                result = fn(self, body, user, *match.groups())
                if isinstance(result, Raw):
                    return self._send(200, result.data, result.ctype, result.headers)
                return self._send(200, result)
            except HttpError as e:
                return self._send(e.code, {"error": str(e)})
            except KeyError as e:
                return self._send(404, {"error": str(e).strip("'")})
            except DriverError as e:
                return self._send(502, {"error": f"switch: {e}"})
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "no such endpoint"})

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


class FirmwareHandler(BaseHTTPRequestHandler):
    """Plain HTTP, read-only, serves only /fw/<token>/<file> so switches can pull images."""
    server_version = "SwitchDeck-FW/0.1"

    def log_message(self, fmt, *args):
        print("[fw] " + fmt % args)

    def do_GET(self):
        rel = unquote(urlparse(self.path).path).removeprefix("/fw/")
        full = os.path.realpath(os.path.join(config.FIRMWARE_DIR, rel))
        if (not full.startswith(os.path.realpath(config.FIRMWARE_DIR) + os.sep)
                or rel.count("/") != 1 or not os.path.isfile(full)):
            self.send_error(404)
            return
        size = os.path.getsize(full)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(full, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)


def serve():
    import ssl
    import threading
    httpd = ThreadingHTTPServer((config.HTTP_HOST, config.HTTP_PORT), Handler)
    cert, key = os.environ.get("SD_TLS_CERT"), os.environ.get("SD_TLS_KEY")
    scheme = "http"
    if cert and key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    fw = ThreadingHTTPServer((config.HTTP_HOST, FW_PORT), FirmwareHandler)
    threading.Thread(target=fw.serve_forever, daemon=True, name="fw-http").start()
    print(f"[web] {scheme}://{config.HTTP_HOST}:{config.HTTP_PORT}  (firmware files on :{FW_PORT})")
    httpd.serve_forever()
