"""Subnet sweep that fingerprints Transition/Lantronix switches without credentials."""
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from .. import db
from ..drivers.base import DriverError, HttpClient

MAX_HOSTS = 1024
SIGNATURES = ("transition", "lantronix", "sispm", "smartview", "lss2200")


def _open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fingerprint(host):
    # LS Series: REST API on 8000 with Swagger at /rest/v1/
    if _open(host, 8000):
        for scheme in ("https", "http"):
            try:
                body = HttpClient(f"{scheme}://{host}:8000", timeout=3).request("GET", "/rest/v1/", raw=True)
                txt = body[:20000].decode(errors="replace").lower()
                if "swagger" in txt or "rest" in txt or "lss" in txt:
                    return {"family": "lss", "port": 8000, "scheme": scheme, "hint": "REST v1 on :8000"}
            except DriverError:
                continue
    for port, scheme in ((80, "http"), (443, "https")):
        if not _open(host, port):
            continue
        try:
            c = HttpClient(f"{scheme}://{host}:{port}", timeout=3)
            body = c.request("GET", "/", raw=True)[:20000].decode(errors="replace").lower()
        except DriverError as e:
            body = str(e).lower()
        if any(s in body for s in SIGNATURES):
            return {"family": "sispm", "port": port, "scheme": scheme, "hint": "web UI signature"}
        # Unified API answers on /api/ even before login on many builds.
        try:
            HttpClient(f"{scheme}://{host}:{port}", timeout=3).request("GET", "/api/get_sysinfo")
            return {"family": "sispm", "port": port, "scheme": scheme, "hint": "/api/ responded"}
        except DriverError as e:
            if "HTTP 401" in str(e) or "HTTP 403" in str(e):
                return {"family": "sispm", "port": port, "scheme": scheme, "hint": "/api/ requires login"}
    return None


def scan(job, cidr):
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > MAX_HOSTS:
        raise ValueError(f"{cidr} has {len(hosts)} hosts; limit is {MAX_HOSTS} (use /22 or smaller)")
    known = {r["host"] for r in db.q("SELECT host FROM devices")}
    found = []
    done = 0

    def probe(h):
        nonlocal done
        r = None if h in known else _fingerprint(h)
        done += 1
        job["progress"] = f"{done}/{len(hosts)} scanned, {len(found)} found"
        if r:
            found.append(h)
            db.x("INSERT INTO discovered(host, family, port, scheme, hint, ts) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(host) DO UPDATE SET family=excluded.family, port=excluded.port, "
                 "scheme=excluded.scheme, hint=excluded.hint, ts=excluded.ts",
                 (h, r["family"], r["port"], r["scheme"], r["hint"], time.time()))

    with ThreadPoolExecutor(max_workers=64) as ex:
        list(ex.map(probe, hosts))
    return {"scanned": len(hosts), "found": found}
