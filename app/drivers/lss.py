"""Driver for the Lantronix LS Series (LSS2200-8P, "EOS") REST API.

Documented in REST API User Guide 33863 Rev C:
  * base  https://<ip>:8000/rest/v1/   (port fixed at 8000)
  * auth  JWT, sent as `Authorization: Bearer <token>`; token invalidates if the switch clock changes
  * built-in Swagger UI at the base path; the schema documents every endpoint
  * known resources: sysinfo/, banner/, users/, user/{name}, date_time, poe/config/port,
    lldp/neighbors, logical_ports, user_settings/change_password, consoleflow/config/connections
  * many endpoints need a trailing '/'

The guide does not print the login path or most response field names, so this driver:
  1. tries a short list of login conventions and remembers the one that works, and
  2. reads the switch's own OpenAPI schema to find status/config endpoints.
Field mapping is case-insensitive and tolerant. Use the API Explorer in the UI to confirm
names on a lab unit; adjust CANDIDATES below if your firmware differs.
"""
import time

from .base import AuthError, Driver, DriverError, HttpClient, pick

CANDIDATES = {
    "login_paths": ["login/", "login", "auth/login/", "auth/login", "user/login/", "token/"],
    "schema_paths": ["swagger.json", "openapi.json", "swagger/v1/swagger.json"],
    "ports": ["ports/", "port/status/", "interfaces/", "physical_ports/", "port_status/"],
    "port_stats": ["ports/statistics/", "port/statistics/", "statistics/ports/", "port_counters/"],
    "poe_status": ["poe/status/port/", "poe/status/ports/", "poe/status/"],
    "poe_config": ["poe/config/port/", "poe/config/ports/"],
    "lldp": ["lldp/neighbors/", "lldp/neighbors"],
    "save": ["config/save/", "system/save_config/", "save_config/", "config/write/"],
    "reboot": ["reboot/", "system/reboot/"],
}

VOLATILE_HINTS = ("UPTIME", "DATE", "TIME", "COUNTER", "STATISTIC", "RX_", "TX_", "TEMP", "LOAD", "STATUS")


def _rows(obj):
    """Return a list of dict rows from either a list or a dict keyed by port."""
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    if isinstance(obj, dict):
        for k in ("ports", "PORTS", "data", "DATA", "items", "interfaces"):
            if isinstance(obj.get(k), (list, dict)):
                return _rows(obj[k])
        if obj and all(isinstance(v, dict) for v in obj.values()):
            return [dict(v, _key=k) for k, v in obj.items()]
    return []


COPPER_PORTS = 8  # LSS2200-8P: ge1-ge8 copper, xe1-xe2 SFP+ -> ports 9-10


def _port_no(row):
    v = str(pick(row, "PORT", "PORT_ID", "ID", "PORT_NUM", "PORT_NO", "NAME", "PORT_NAME", "_key", default=""))
    digits = "".join(ch for ch in v.split("/")[-1] if ch.isdigit())
    if not digits:
        return None
    n = int(digits)
    if v.lower().lstrip().startswith(("xe", "te", "sfp", "10g")):
        n += COPPER_PORTS
    return n


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not any(h in k.upper() for h in VOLATILE_HINTS)}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


class LssDriver(Driver):
    family = "lss"
    default_port = 8000
    capabilities = {"poe", "poe_cycle", "backup", "neighbors", "reboot"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.scheme = self.scheme or "https"
        self.http = HttpClient(f"{self.scheme}://{self.host}:{self.port}/rest/v1")
        self.token = None
        self._login_path = None
        self._schema = None
        self._resolved = {}

    # ---------------------------------------------------------------- session
    def login(self):
        paths = [self._login_path] if self._login_path else CANDIDATES["login_paths"]
        bodies = [{"username": self.username, "password": self.password},
                  {"USERNAME": self.username, "PASSWORD": self.password}]
        last = None
        for path in paths:
            for body in bodies:
                try:
                    r = self.http.request("POST", path, body)
                except AuthError as e:
                    last = e
                    continue
                except DriverError as e:
                    last = e
                    if "HTTP 404" in str(e) or "HTTP 405" in str(e):
                        break
                    continue
                tok = pick(r or {}, "access_token", "token", "key", "jwt")
                if tok:
                    self.token = tok
                    self._login_path = path
                    self.http.headers["Authorization"] = f"Bearer {tok}"
                    return
        raise AuthError(f"login failed ({last})")

    def _call(self, method, path, body=None):
        if not self.token:
            self.login()
        try:
            return self.http.request(method, path, body)
        except AuthError:
            # Token is time-based; any clock change on the switch invalidates it.
            self.token = None
            self.login()
            return self.http.request(method, path, body)

    def raw(self, method, path, body=None):
        return self._call(method.upper(), path.lstrip("/").removeprefix("rest/v1/"), body)

    # ---------------------------------------------------------------- schema discovery
    def schema(self):
        if self._schema is None:
            self._schema = {}
            for p in CANDIDATES["schema_paths"]:
                try:
                    s = self._call("GET", p)
                    if isinstance(s, dict) and "paths" in s:
                        self._schema = s
                        break
                except DriverError:
                    continue
        return self._schema

    def endpoints(self, method="get"):
        paths = (self.schema() or {}).get("paths", {})
        return sorted(p.lstrip("/") for p, ops in paths.items() if method in ops and "{" not in p)

    def _resolve(self, kind, method="GET"):
        if kind in self._resolved:
            return self._resolved[kind]
        known = set(e.strip("/") for e in self.endpoints(method.lower()))
        cands = CANDIDATES[kind]
        ordered = [c for c in cands if c.strip("/") in known] + [c for c in cands if c.strip("/") not in known]
        for c in ordered:
            if method != "GET" and known and c.strip("/") not in known:
                continue
            if method == "GET":
                try:
                    data = self._call("GET", c)
                    self._resolved[kind] = (c, data)
                    return self._resolved[kind]
                except DriverError:
                    continue
            else:
                self._resolved[kind] = (c, None)
                return self._resolved[kind]
        self._resolved[kind] = (None, None)
        return self._resolved[kind]

    def _get(self, kind):
        path, cached = self._resolve(kind)
        if not path:
            return None
        if cached is not None:
            self._resolved[kind] = (path, None)
            return cached
        try:
            return self._call("GET", path)
        except DriverError:
            return None

    # ---------------------------------------------------------------- read
    def status(self):
        si = self._call("GET", "sysinfo/") or {}
        system = {
            "model": pick(si, "MODEL", "MODEL_NAME", "PRODUCT", default="LSS2200-8P"),
            "name": pick(si, "SYSNAME", "SYSTEM_NAME", "HOSTNAME", default=""),
            "location": pick(si, "LOCATION", default=""),
            "contact": pick(si, "CONTACT", default=""),
            "firmware": pick(si, "FIRMWARE", "FW_VERSION", "SOFTWARE_VERSION", "VERSION", default=""),
            "mac": str(pick(si, "MAC", "MAC_ADDRESS", "BASE_MAC", default="")).replace("-", ":").lower(),
            "serial": pick(si, "SERIAL", "SERIAL_NUMBER", default=""),
            "uptime": pick(si, "UPTIME", default=""),
            "cpu": pick(si, "CPU", "CPU_LOAD", "CPU_UTILIZATION"),
            "temp": pick(si, "TEMPERATURE", "TEMP"),
        }

        ports = {}
        for row in _rows(self._get("ports")):
            n = _port_no(row)
            if n is None:
                continue
            link = str(pick(row, "LINK", "LINK_STATUS", "OPER_STATUS", "STATUS", default="down")).lower()
            ports[n] = {
                "id": n, "name": str(pick(row, "NAME", "PORT_NAME", default=f"Port {n}")),
                "media": "fiber" if n > 8 else "copper",
                "link_up": link in ("up", "true", "1", "connected", "link up"),
                "speed": str(pick(row, "SPEED", "OPER_SPEED", "LINK_SPEED", default="")),
                "admin_up": str(pick(row, "ADMIN", "ADMIN_STATUS", "ENABLED", "STATE", default="up")).lower()
                             not in ("down", "false", "disabled", "0"),
                "description": pick(row, "DESCRIPTION", "ALIAS", default=""),
                "rx_bytes": pick(row, "RX_BYTES", "RX_OCTETS", "IN_OCTETS", default=0),
                "tx_bytes": pick(row, "TX_BYTES", "TX_OCTETS", "OUT_OCTETS", default=0),
                "rx_errors": pick(row, "RX_ERRORS", "IN_ERRORS", default=0),
                "rx_drops": pick(row, "RX_DROPS", "IN_DISCARDS", default=0),
                "tx_drops": pick(row, "TX_DROPS", "OUT_DISCARDS", default=0),
                "poe": None, "sfp": None,
            }

        poe = None
        rows = _rows(self._get("poe_status"))
        cfg_rows = {_port_no(r): r for r in _rows(self._get("poe_config"))}
        if rows or cfg_rows:
            used = 0.0
            for row in rows or cfg_rows.values():
                n = _port_no(row)
                if n is None:
                    continue
                w = float(pick(row, "POWER", "POWER_W", "POWER_USED", "CONSUMED_POWER", default=0) or 0)
                used += w
                st = str(pick(row, "STATUS", "POE_STATUS", "STATE", default=""))
                mode = str(pick(cfg_rows.get(n, {}), "POE-ENABLE", "POE_ENABLE", "MODE", default=""))
                p = ports.setdefault(n, {"id": n, "name": f"Port {n}", "media": "copper", "link_up": False,
                                         "speed": "", "admin_up": True, "description": "", "rx_bytes": 0,
                                         "tx_bytes": 0, "rx_errors": 0, "rx_drops": 0, "tx_drops": 0,
                                         "poe": None, "sfp": None})
                p["poe"] = {"status": st, "delivering": w > 0 or "deliver" in st.lower(), "power_w": w,
                            "class": str(pick(row, "CLASS", "PD_CLASS", default="-")),
                            "priority": str(pick(cfg_rows.get(n, {}), "PRIORITY", default="")), "mode": mode}
            poe = {"budget_w": None, "used_w": round(used, 1)}

        neighbors = []
        for row in _rows(self._get("lldp")):
            neighbors.append({
                "port": _port_no({"PORT": pick(row, "PORT_NAME", "LOCAL_PORT", "PORT", default="")}),
                "name": pick(row, "SYSTEM_NAME", "SYSNAME", default=""),
                "type": "LLDP", "model": pick(row, "SYSTEM_DESCRIPTION", default="")[:60],
                "ip": pick(row, "MGMT_ADDRESS", "MANAGEMENT_ADDRESS", "IP", default=""),
                "mac": str(pick(row, "CHASSIS_ID", "MAC", default="")).lower(),
            })

        return {"system": system, "ports": [ports[k] for k in sorted(ports)], "poe": poe,
                "neighbors": neighbors, "mac_count": 0}

    def backup(self):
        out = {"_family": self.family, "_format": 1, "_endpoints": []}
        paths = [p for p in self.endpoints("get")
                 if any(k in p for k in ("config", "sysinfo", "banner", "users", "logical_ports", "vlan", "snmp",
                                         "ntp", "syslog", "lldp/config", "dns"))]
        if not paths:  # schema unavailable: fall back to documented resources
            paths = ["sysinfo/", "banner/", "users/", "poe/config/port", "logical_ports"]
        for p in paths:
            try:
                out[p.strip("/")] = _strip(self._call("GET", p if p.endswith("/") else p + "/"))
                out["_endpoints"].append(p)
            except DriverError as e:
                out[p.strip("/")] = {"_unsupported": str(e)[:200]}
        return out

    # ---------------------------------------------------------------- write
    def poe_cycle(self, port, off_seconds=5):
        path, _ = self._resolve("poe_config")
        if not path:
            raise DriverError("PoE config endpoint not found on this firmware")
        rows = {_port_no(r): r for r in _rows(self._call("GET", path))}
        if port not in rows:
            raise DriverError(f"port {port} has no PoE entry")
        row = {k: v for k, v in rows[port].items() if k != "_key"}
        key = next((k for k in row if k.upper().replace("_", "-") == "POE-ENABLE"), "POE-ENABLE")
        original = row.get(key, "enabled")
        row.setdefault("PORT", port)
        self._call("POST", path, dict(row, **{key: "disabled"}))
        time.sleep(off_seconds)
        self._call("POST", path, dict(row, **{key: original}))
        return f"PoE on port {port} cycled ({off_seconds}s off), restored to {original}"

    def set_identity(self, name=None, location=None, contact=None):
        body = {}
        if name is not None:
            body["SYSNAME"] = name
        if location is not None:
            body["LOCATION"] = location
        if contact is not None:
            body["CONTACT"] = contact
        self._call("POST", "sysinfo/", body)

    def save(self):
        path, _ = self._resolve("save", "POST")
        if not path:
            raise DriverError("save endpoint not found; check the switch's Swagger UI")
        return self._call("POST", path, {})

    def reboot(self):
        path, _ = self._resolve("reboot", "POST")
        if not path:
            raise DriverError("reboot endpoint not found; check the switch's Swagger UI")
        try:
            self._call("POST", path, {})
        except DriverError as e:
            if "unreachable" not in str(e):
                raise
        self.token = None
