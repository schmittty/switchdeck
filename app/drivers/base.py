"""Driver base class and a small HTTP client (stdlib only).

Every driver returns the same normalized shapes so the poller, UI and backup engine never
need to know which switch family they're talking to.

Normalized status:
{
  "system": {model, name, location, contact, firmware, mac, serial, uptime, cpu, temp,
             ram_total_kb, ram_free_kb},
  "ports":  [{id, name, media, link_up, speed, admin_up, description,
              rx_bytes, tx_bytes, rx_errors, rx_drops, tx_drops,
              poe: {mode, status, delivering, power_w, class, priority} | None,
              sfp: {...} | None}],
  "poe":    {budget_w, used_w} | None,
  "neighbors": [{port, name, type, model, ip, mac}],
  "mac_count": int
}
"""
import http.cookiejar
import json
import ssl
import urllib.error
import urllib.request

from .. import config


class DriverError(Exception):
    pass


class AuthError(DriverError):
    pass


class HttpClient:
    def __init__(self, base_url: str, timeout: float = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or config.DEVICE_TIMEOUT
        self.cookies = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        if not config.VERIFY_TLS:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self.headers = {"Accept": "application/json"}

    def request(self, method: str, path: str, body=None, headers=None, raw=False):
        url = self.base_url + (path if path.startswith("/") else "/" + path)
        data = None
        h = dict(self.headers)
        h.update(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            text = e.read()[:500].decode(errors="replace")
            if e.code in (401, 403):
                raise AuthError(f"{e.code} {text}")
            raise DriverError(f"HTTP {e.code} on {method} {path}: {text}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise DriverError(f"unreachable ({getattr(e, 'reason', e)})")
        if raw:
            return payload
        if not payload.strip() or payload.strip() == b"null":
            return None
        try:
            return json.loads(payload)
        except ValueError:
            # Some firmware returns typographic quotes or trailing commas; try a tolerant pass.
            txt = payload.decode(errors="replace").replace("“", '"').replace("”", '"')
            try:
                return json.loads(txt)
            except ValueError:
                raise DriverError(f"non-JSON reply from {path}: {txt[:200]}")


class Driver:
    family = "base"
    default_port = 80
    capabilities: set = set()

    def __init__(self, host, username, password, port=None, scheme="http"):
        self.host = host
        self.username = username
        self.password = password
        self.port = port or self.default_port
        self.scheme = scheme

    # --- lifecycle
    def close(self):
        pass

    # --- read
    def status(self) -> dict:
        raise NotImplementedError

    def backup(self) -> dict:
        """Return a JSON-serializable, volatile-field-free snapshot of configuration."""
        raise NotImplementedError

    def raw(self, method: str, path: str, body=None):
        """Pass-through for the API explorer."""
        raise NotImplementedError

    # --- write (each raises DriverError if unsupported)
    def poe_cycle(self, port: int, off_seconds: int = 5):
        raise DriverError("PoE cycle not supported on this family")

    def set_port(self, port: int, enabled=None, description=None):
        raise DriverError("port config not supported on this family")

    def set_identity(self, name=None, location=None, contact=None):
        raise DriverError("not supported")

    def set_ntp(self, servers):
        raise DriverError("not supported")

    def set_syslog(self, host, port=514):
        raise DriverError("not supported")

    def save(self):
        raise DriverError("not supported")

    def reboot(self):
        raise DriverError("not supported")

    def restore(self, snapshot: dict, sections=None) -> list:
        raise DriverError("restore not supported on this family")

    def firmware_upgrade(self, url: str):
        raise DriverError("firmware upgrade not supported on this family")

    def firmware_status(self) -> str:
        return "unknown"

    def cable_test(self, port: int) -> dict:
        raise DriverError("cable diagnostics not supported on this family")


def pick(d: dict, *names, default=None):
    """Case-insensitive lookup of the first matching key."""
    if not isinstance(d, dict):
        return default
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return default
