"""Device manager: driver sessions, polling, config backups and change detection."""
import difflib
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import config, db, security
from ..drivers import DriverError, make_driver

_drivers = {}
_locks = {}
_glock = threading.Lock()
_prev_counters = {}   # device_id -> (ts, {port: (rx, tx)})
_stop = threading.Event()


# ------------------------------------------------------------------ driver sessions
def device(device_id):
    d = db.q1("SELECT * FROM devices WHERE id=?", (device_id,))
    if not d:
        raise KeyError(f"device {device_id} not found")
    return d


def lock(device_id):
    with _glock:
        return _locks.setdefault(device_id, threading.RLock())


def driver(device_id):
    with _glock:
        drv = _drivers.get(device_id)
    if drv is None:
        d = device(device_id)
        secret = d["secret"]
        if d.get("credential_id"):  # shared credential profile overrides the per-device login
            c = db.q1("SELECT username, secret FROM credentials WHERE id=?", (d["credential_id"],))
            if c:
                d = dict(d, username=c["username"])
                secret = c["secret"]
        drv = make_driver(d, security.decrypt(secret))
        with _glock:
            _drivers[device_id] = drv
    return drv


def forget(device_id):
    with _glock:
        drv = _drivers.pop(device_id, None)
    if drv:
        try:
            drv.close()
        except Exception:  # noqa: BLE001
            pass


def run(device_id, fn):
    """Run fn(driver) under the device lock (serializes poller and user actions per switch)."""
    with lock(device_id):
        return fn(driver(device_id))


# ------------------------------------------------------------------ polling
def poll(device_id):
    d = device(device_id)
    now = time.time()
    try:
        st = run(device_id, lambda drv: drv.status())
    except (DriverError, Exception) as e:  # noqa: BLE001
        msg = str(e)[:300]
        if d["status"] != "offline":
            db.event(device_id, "error", f"{d['name']} went offline: {msg}", "poller")
        db.x("UPDATE devices SET status='offline', last_error=? WHERE id=?", (msg, device_id))
        forget(device_id)
        return None

    # Per-port throughput from counter deltas.
    prev_ts, prev = _prev_counters.get(device_id, (None, {}))
    cur = {}
    rx_total = tx_total = 0.0
    for p in st["ports"]:
        rx, tx = int(p.get("rx_bytes") or 0), int(p.get("tx_bytes") or 0)
        cur[p["id"]] = (rx, tx)
        p["rx_bps"] = p["tx_bps"] = 0
        if prev_ts and p["id"] in prev:
            dt = max(now - prev_ts, 1)
            drx, dtx = rx - prev[p["id"]][0], tx - prev[p["id"]][1]
            if drx >= 0 and dtx >= 0:  # counters reset on reboot
                p["rx_bps"], p["tx_bps"] = drx * 8 / dt, dtx * 8 / dt
        rx_total += p["rx_bps"]
        tx_total += p["tx_bps"]
    _prev_counters[device_id] = (now, cur)
    st["rx_bps"], st["tx_bps"] = rx_total, tx_total

    # Change events: link transitions, PoE budget.
    old = db.q1("SELECT data FROM snapshots WHERE device_id=?", (device_id,))
    if old:
        old = json.loads(old["data"])
        old_ports = {p["id"]: p for p in old.get("ports", [])}
        for p in st["ports"]:
            o = old_ports.get(p["id"])
            if o and o.get("link_up") != p.get("link_up"):
                db.event(device_id, "info" if p["link_up"] else "warning",
                         f"{d['name']} port {p['id']} link {'up ' + p.get('speed', '') if p['link_up'] else 'down'}",
                         "poller")
    poe = st.get("poe") or {}
    if poe.get("budget_w") and poe.get("used_w") is not None:
        pct = poe["used_w"] / poe["budget_w"] * 100
        was = ((old or {}).get("poe") or {})
        was_pct = (was.get("used_w", 0) / was["budget_w"] * 100) if was.get("budget_w") else 0
        if pct >= 90 > was_pct:
            db.event(device_id, "warning", f"{d['name']} PoE budget at {pct:.0f}%", "poller")

    db.x("INSERT INTO snapshots(device_id, ts, data) VALUES (?,?,?) "
         "ON CONFLICT(device_id) DO UPDATE SET ts=excluded.ts, data=excluded.data",
         (device_id, now, json.dumps(st)))
    s = st["system"]
    db.x("INSERT INTO metrics(device_id, ts, cpu, temp, poe_w, rx_bps, tx_bps) VALUES (?,?,?,?,?,?,?)",
         (device_id, now, _num(s.get("cpu")), _num(s.get("temp")), poe.get("used_w"), rx_total, tx_total))
    if d["status"] == "offline":
        db.event(device_id, "info", f"{d['name']} is back online", "poller")
    if s.get("firmware") and d["firmware"] and s["firmware"] != d["firmware"]:
        db.event(device_id, "info", f"{d['name']} firmware changed {d['firmware']} → {s['firmware']}", "poller")
    db.x("UPDATE devices SET status='online', last_seen=?, last_error='', model=?, mac=?, serial=?, firmware=? "
         "WHERE id=?", (now, s.get("model") or d["model"], s.get("mac") or d["mac"],
                        s.get("serial") or d["serial"], s.get("firmware") or d["firmware"], device_id))
    return st


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def poll_all():
    ids = [r["id"] for r in db.q("SELECT id FROM devices")]
    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(poll, ids))


# ------------------------------------------------------------------ backups
def canonical(snapshot):
    return json.dumps(snapshot, sort_keys=True, indent=2, ensure_ascii=False)


def backup(device_id, trigger="manual", note=""):
    d = device(device_id)
    snap = run(device_id, lambda drv: drv.backup())
    text = canonical(snap)
    sha = hashlib.sha256(text.encode()).hexdigest()
    last = db.q1("SELECT id, sha, content FROM backups WHERE device_id=? ORDER BY ts DESC LIMIT 1", (device_id,))
    if last and last["sha"] == sha and trigger == "scheduled":
        return {"changed": False, "id": last["id"]}
    bid = db.x("INSERT INTO backups(device_id, ts, sha, content, trigger, note) VALUES (?,?,?,?,?,?)",
               (device_id, time.time(), sha, text, trigger, note))
    if last and last["sha"] != sha:
        added, removed = diff_stats(last["content"], text)
        db.event(device_id, "warning", f"{d['name']} configuration changed (+{added}/-{removed} lines)", "backup")
    return {"changed": not last or last["sha"] != sha, "id": bid}


def diff(old_text, new_text, a="previous", b="current"):
    return "".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True), a, b, n=3))


def diff_stats(old_text, new_text):
    added = removed = 0
    for line in difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def backup_all(trigger="scheduled"):
    for r in db.q("SELECT id, name FROM devices WHERE status='online'"):
        try:
            backup(r["id"], trigger)
        except Exception as e:  # noqa: BLE001
            db.event(r["id"], "error", f"Backup of {r['name']} failed: {e}", "backup")


# ------------------------------------------------------------------ background loops
def _loop(interval, fn, name):
    def body():
        while not _stop.is_set():
            t0 = time.time()
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                print(f"[{name}] {e}")
            _stop.wait(max(1.0, interval - (time.time() - t0)))
    threading.Thread(target=body, daemon=True, name=name).start()


def _housekeeping():
    db.x("DELETE FROM metrics WHERE ts < ?", (time.time() - 7 * 86400,))
    db.x("DELETE FROM events WHERE ts < ?", (time.time() - 90 * 86400,))
    keep = int(db.setting("backup_retention", 60))
    for r in db.q("SELECT id FROM devices"):
        db.x("DELETE FROM backups WHERE device_id=? AND id NOT IN "
             "(SELECT id FROM backups WHERE device_id=? ORDER BY ts DESC LIMIT ?)", (r["id"], r["id"], keep))


def start():
    _loop(config.POLL_INTERVAL, poll_all, "poller")

    def delayed_backups():
        _stop.wait(60)  # let the first polls land
        _loop(config.BACKUP_INTERVAL, backup_all, "backups")
    threading.Thread(target=delayed_backups, daemon=True).start()
    _loop(3600, _housekeeping, "housekeeping")


def stop():
    _stop.set()
    for k in list(_drivers):
        forget(k)
