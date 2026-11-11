"""Driver for the Transition Networks / Lantronix SISPM1040 family "Unified API".

Covers SISPM1040-582-LRT (unified API from FW B7.20.0039, HTTPS from B7.20.0191),
SISPM1040-384-LRT-C and SISPM1040-362-LRT. Endpoints per API User Guide 33824 Rev C.

Auth model: the client chooses a session id, POSTs it to /api/login, then sends it on every
request as the cookie `seid`.
"""
import random
import re
import time

from .base import AuthError, Driver, DriverError, HttpClient

# Sections captured in a backup: name -> (GET path, SET path or None, volatile keys to strip)
SECTIONS = {
    "sysinfo":        ("/api/get_sysinfo", "/api/set_sysinfo", None),
    "ports":          ("/api/get_port_config", "/api/set_port_config", {"link"}),
    "poe":            ("/api/get_poe_config", "/api/set_poe_config", {"total_power_watts", "poe_firmware_version", "profile_list"}),
    "poe_auto_reset": ("/api/get_poe_auto_reset", "/api/set_poe_auto_reset", {"error", "total"}),
    "vlan":           ("/api/get_vlan_config", "/api/set_vlan_config", None),
    "mac_vlan":       ("/api/get_mac_based_vlan", None, None),
    "ip":             ("/api/get_ip_address", "/api/set_ip_address", {"current_lease"}),
    "mirror":         ("/api/get_mirror_config", "/api/set_mirror_config", None),
    "ntp":            ("/api/get_ntp_server", "/api/set_ntp_server", None),
    "syslog":         ("/api/get_syslog_server", "/api/set_syslog_server", None),
    "time":           ("/api/get_system_time", "/api/set_system_time", {"system_date"}),
    "snmp_trap":      ("/api/get_snmp_trap_config", None, None),
    "accounts":       ("/api/get_account_config", None, None),
    "acl":            ("/api/get_acl_config", None, {"counter"}),
    "di_do":          ("/api/get_di_do_config", "/api/set_di_do_config", None),
}

# Sections that are safe to replay on restore. `ip` is opt-in only: a wrong address strands the switch.
RESTORABLE = ["sysinfo", "ports", "poe", "poe_auto_reset", "vlan", "mirror", "ntp", "syslog", "time", "di_do"]

SPEED = re.compile(r"^(\d+(?:\.\d+)?)([GM])", re.I)


def _strip(obj, keys):
    if not keys:
        return obj
    if isinstance(obj, dict):
        return {k: _strip(v, keys) for k, v in obj.items() if k not in keys}
    if isinstance(obj, list):
        return [_strip(v, keys) for v in obj]
    return obj


def _int(v, default=0):
    m = re.search(r"-?\d+", str(v))
    return int(m.group()) if m else default


class SispmDriver(Driver):
    family = "sispm"
    default_port = 80
    capabilities = {"poe", "poe_cycle", "port_admin", "backup", "restore", "firmware", "reboot",
                    "cable_test", "ntp", "syslog", "neighbors"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        default = 443 if self.scheme == "https" else 80
        self.http = HttpClient(f"{self.scheme}://{self.host}:{self.port or default}")
        self.sessid = None

    # ---------------------------------------------------------------- session
    def login(self):
        self.sessid = str(random.randint(100_000_000, 999_999_999))
        self.http.headers["Cookie"] = f"seid={self.sessid}"
        r = self.http.request("POST", "/api/login", {"login": {
            "username": self.username, "password": self.password,
            "user_ip": "0.0.0.0", "sessid": self.sessid}})
        status = ((r or {}).get("response") or {}).get("status", "")
        if status and status.lower() != "success":
            self.sessid = None
            raise AuthError(f"login rejected: {(r.get('response') or {}).get('message') or status}")

    def close(self):
        if self.sessid:
            try:
                self.http.request("POST", "/api/logout", {"logout": {"sessid": self.sessid}})
            except DriverError:
                pass
            self.sessid = None

    def _call(self, method, path, body=None):
        if not self.sessid:
            self.login()
        try:
            r = self.http.request(method, path, body)
        except AuthError:
            self.login()
            r = self.http.request(method, path, body)
        # Some firmware answers 200 with a failure envelope when the session expired.
        resp = (r or {}).get("response") if isinstance(r, dict) else None
        if isinstance(resp, dict) and str(resp.get("status", "")).lower() in ("fail", "error"):
            msg = str(resp.get("message", ""))
            if "login" in msg.lower() or "session" in msg.lower():
                self.login()
                return self.http.request(method, path, body)
            raise DriverError(f"{path}: {msg or 'failed'}")
        return r

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, body):
        return self._call("POST", path, body)

    def raw(self, method, path, body=None):
        return self._call(method.upper(), path, body)

    # ---------------------------------------------------------------- read
    def status(self):
        info = (self.get("/api/get_sysinfo") or {}).get("system", {}).get("information", {})
        ram = info.get("ram") or {}
        system = {
            "model": info.get("model_name", ""),
            "description": info.get("description", ""),
            "name": info.get("system_name", ""),
            "location": info.get("location", ""),
            "contact": info.get("contact", ""),
            "firmware": info.get("firmware_version", ""),
            "hardware": info.get("hardware_version", ""),
            "mac": info.get("mac_addr", "").replace("-", ":").lower(),
            "serial": info.get("serial_number", ""),
            "uptime": info.get("uptime", ""),
            "cpu": _int(info.get("cpu_load", "0")),
            "temp": info.get("temperature_1") or info.get("temperature"),
            "temp2": info.get("temperature_2"),
            "ram_total_kb": _int(ram.get("total", 0)),
            "ram_free_kb": _int(ram.get("free", 0)),
        }

        ports = {}
        for p in (self.get("/api/get_port_config") or {}).get("ports", []):
            link = str(p.get("link", "down"))
            m = SPEED.match(link)
            ports[p["id"]] = {
                "id": p["id"], "name": f"Port {p['id']}", "media": p.get("media", "copper"),
                "link_up": link.lower() != "down" and bool(m),
                "speed": (m.group(1) + m.group(2).upper()) if m else "",
                "duplex": "full" if "fdx" in link.lower() else ("half" if "hdx" in link.lower() else ""),
                "admin_up": p.get("speed_mode", "Auto") != "Disabled",
                "speed_mode": p.get("speed_mode", ""),
                "description": p.get("description", ""),
                "rx_bytes": 0, "tx_bytes": 0, "rx_errors": 0, "rx_drops": 0, "tx_drops": 0,
                "poe": None, "sfp": None,
            }

        try:
            for p in (self.get("/api/get_port_statistics") or {}).get("ports", []):
                st = p.get("statistics", {})
                rx, tx = st.get("rx_packets", {}), st.get("tx_packets", {})
                if p["id"] in ports:
                    ports[p["id"]].update({
                        "rx_bytes": rx.get("octets", 0), "tx_bytes": tx.get("octets", 0),
                        "rx_errors": rx.get("crc_alignment", 0) + rx.get("fragments", 0) + rx.get("jabber", 0),
                        "rx_drops": rx.get("drop", 0), "tx_drops": tx.get("drop", 0),
                    })
        except DriverError:
            pass

        poe = None
        try:
            ps = self.get("/api/get_poe_status") or {}
            pcfg = self.get("/api/get_poe_config") or {}
            pc = {p["id"]: p.get("poe", {}) for p in pcfg.get("ports", [])}
            cfg = pcfg.get("poe", {})
            budget = cfg.get("total_power_watts")
            # Guide: AT firmware reports budget in 0.1 W; BT firmware in watts (has poe_firmware_version).
            if budget is not None and "poe_firmware_version" not in cfg:
                budget = budget / 10
            totals = ps.get("poe", {})
            poe = {"budget_w": budget, "used_w": totals.get("total_power_used", 0) / 10}
            for p in ps.get("ports", []):
                s = p.get("poe", {})
                if p["id"] in ports:
                    st = s.get("port_status", "")
                    ports[p["id"]]["poe"] = {
                        "status": st,
                        "delivering": "on" in st.lower() and "off" not in st.lower(),
                        "power_w": s.get("power_used", 0) / 10,
                        "class": s.get("pd_class", "-"),
                        "priority": s.get("priority", ""),
                        "mode": pc.get(p["id"], {}).get("mode", ""),
                    }
        except DriverError:
            pass

        try:
            for p in (self.get("/api/get_sfp_port_detail") or {}).get("ports", []):
                pid = _int(p.get("id"))
                if pid in ports:
                    ports[pid]["sfp"] = p.get("sfp")
        except DriverError:
            pass

        neighbors = []
        try:
            for sw in (self.get("/api/dev_list_table") or {}).get("device_list_table", []):
                for d in sw.get("device_list", []):
                    neighbors.append({
                        "port": d.get("port_no"), "name": d.get("device_name", ""),
                        "type": d.get("device_type", ""), "model": d.get("model_name", ""),
                        "ip": d.get("ip_addr", ""), "mac": d.get("mac", "").replace("-", ":").lower(),
                        "alarms": d.get("number_of_alarm_events", 0),
                    })
        except DriverError:
            pass

        mac_count = 0
        try:
            mac_count = len((self.get("/api/get_dynamic_mac_table") or {}).get("mac_table", []))
        except DriverError:
            pass

        return {"system": system, "ports": [ports[k] for k in sorted(ports)], "poe": poe,
                "neighbors": neighbors, "mac_count": mac_count}

    def backup(self):
        out = {"_family": self.family, "_format": 1}
        for name, (getp, _setp, volatile) in SECTIONS.items():
            try:
                data = self.get(getp)
            except DriverError as e:
                out[name] = {"_unsupported": str(e)[:200]}
                continue
            if name == "sysinfo":
                info = (data or {}).get("system", {}).get("information", {})
                data = {"system": {"information": {k: info.get(k, "") for k in ("system_name", "location", "contact")}}}
            out[name] = _strip(data, volatile)
        return out

    # ---------------------------------------------------------------- write
    def _poe_ports(self):
        return {p["id"]: p for p in (self.get("/api/get_poe_config") or {}).get("ports", [])}

    def poe_cycle(self, port, off_seconds=5):
        ports = self._poe_ports()
        if port not in ports:
            raise DriverError(f"port {port} has no PoE")
        original = dict(ports[port]["poe"])
        if original.get("mode") == "Disabled":
            raise DriverError(f"PoE is administratively disabled on port {port}")
        off = dict(original, mode="Disabled")
        off.pop("legacy", None)
        self.post("/api/set_poe_config", {"ports": [{"id": port, "poe": off}]})
        time.sleep(off_seconds)
        on = dict(original)
        on.pop("legacy", None)
        self.post("/api/set_poe_config", {"ports": [{"id": port, "poe": on}]})
        return f"PoE on port {port} cycled ({off_seconds}s off), restored to {original.get('mode')}"

    def set_poe_mode(self, port, mode):
        ports = self._poe_ports()
        cur = dict(ports[port]["poe"], mode=mode)
        cur.pop("legacy", None)
        self.post("/api/set_poe_config", {"ports": [{"id": port, "poe": cur}]})

    def set_port(self, port, enabled=None, description=None):
        current = {p["id"]: p for p in (self.get("/api/get_port_config") or {}).get("ports", [])}
        if port not in current:
            raise DriverError(f"no port {port}")
        p = dict(current[port])
        p.pop("link", None)
        if enabled is not None:
            p["speed_mode"] = "Auto" if enabled else "Disabled"
        if description is not None:
            p["description"] = description[:63]
        self.post("/api/set_port_config", {"ports": [p]})

    def set_identity(self, name=None, location=None, contact=None):
        info = (self.get("/api/get_sysinfo") or {}).get("system", {}).get("information", {})
        body = {"system_name": info.get("system_name", ""), "location": info.get("location", ""),
                "contact": info.get("contact", "")}
        if name is not None:
            body["system_name"] = name
        if location is not None:
            body["location"] = location
        if contact is not None:
            body["contact"] = contact
        self.post("/api/set_sysinfo", {"system": {"information": body}})

    def set_ntp(self, servers):
        s = (list(servers) + [""] * 5)[:5]
        self.post("/api/set_ntp_server", {"system": {"ntp": {
            "automatic": True, "interval": 60, **{f"server{i+1}": s[i] for i in range(5)}}}})

    def set_syslog(self, host, port=514):
        self.post("/api/set_syslog_server", {"system": {"syslog": {
            "mode": True, "server_address": host, "server_port": int(port)}}})

    def save(self):
        return self.get("/api/save_configuration")

    def reboot(self):
        try:
            self.post("/api/reboot", {"system": {"warm": "Yes"}})
        except DriverError as e:
            if "unreachable" not in str(e):  # the switch often drops the connection mid-reply
                raise
        self.sessid = None

    def restore(self, snapshot, sections=None):
        if snapshot.get("_family") != self.family:
            raise DriverError("snapshot is from a different switch family")
        sections = sections or RESTORABLE
        done = []
        for name in sections:
            data = snapshot.get(name)
            setp = SECTIONS.get(name, (None, None, None))[1]
            if not setp or not isinstance(data, dict) or "_unsupported" in data:
                continue
            body = data
            if name in ("poe",):
                body = {k: v for k, v in data.items()}
                if "poe" in body:
                    body["poe"] = {k: v for k, v in body["poe"].items()
                                   if k in ("power_determined_mode", "power_management_mode", "capacitor_detection")}
                    if not body["poe"]:
                        body.pop("poe")
            self.post(setp, body)
            done.append(name)
        self.save()
        return done

    def firmware_upgrade(self, url):
        self.post("/api/firmware_upgrade", {"system": {"firmware": {"upgrade_url": url}}})

    def firmware_status(self):
        r = self.get("/api/get_firmware_upgrade_status") or {}
        return r.get("system", {}).get("firmware", {}).get("upgrade_status", "unknown")

    def cable_test(self, port):
        return self.post("/api/cable_diagnostics", {"cable": {"port": int(port)}})
