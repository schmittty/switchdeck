"""Simulated switches for lab-less testing and the demo mode.

  python -m tools.mock_switch --family sispm --model SISPM1040-582-LRT --port 18081
  python -m tools.mock_switch --family lss --port 18084

The SISPM simulator follows API User Guide 33824 (login with client-chosen sessid, `seid` cookie).
The LSS simulator follows guide 33863 where documented; endpoint names it does not document
(ports/, poe/status/port/, config/save/) are this project's best guesses.
"""
import argparse
import copy
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = {
    "SISPM1040-582-LRT": {"copper": 8, "sfp": 2, "budget": 2400, "desc": "Managed Hardened PoE++ Switch (8) 10/100/1000Base-T PoE++ + (2) SFP"},
    "SISPM1040-384-LRT-C": {"copper": 8, "sfp": 4, "budget": 2400, "desc": "Managed Hardened PoE+ Switch (8) 10/100/1000Base-T PoE+ + (4) SFP"},
    "SISPM1040-362-LRT": {"copper": 6, "sfp": 2, "budget": 1800, "desc": "Managed Hardened PoE+ Switch (6) 10/100/1000Base-T PoE+ + (2) SFP"},
}
CAMERAS = ["AXIS P3265", "Hanwha XNV-8080R", "Bosch FLEXIDOME 5100i", "Avigilon H6A", "Ubiquiti AP", "Door controller"]


class SispmState:
    def __init__(self, model, seed):
        rnd = random.Random(seed)
        m = MODELS[model]
        self.model, self.m = model, m
        self.lock = threading.Lock()
        self.sessions = set()
        self.started = time.time()
        self.fw = "B7.20.0191 2023-11-03"
        self.upgrade = None
        mac_tail = rnd.randint(0x100000, 0xFFFFFF)
        self.info = {"model_name": model, "description": m["desc"], "hardware_version": "v1.01",
                     "mechanical_version": "v1.01", "mac_addr": f"00-c0-f2-{mac_tail >> 16 & 255:02x}-{mac_tail >> 8 & 255:02x}-{mac_tail & 255:02x}",
                     "serial_number": f"A0741{rnd.randint(10, 99)}AR{rnd.randint(10000, 99999)}",
                     "system_name": f"{model}-{seed}", "location": "", "contact": ""}
        n = m["copper"] + m["sfp"]
        self.ports = []
        self.poe = []
        self.devices = {}
        for i in range(1, n + 1):
            sfp = i > m["copper"]
            up = rnd.random() < (0.5 if sfp else 0.75)
            self.ports.append({"id": i, "link": ("1Gfdx" if up else "down"), "media": "fiber" if sfp else "copper",
                               "speed_mode": "Auto", "flow_control": False, "jumbo_frames": 9600, "description": ""})
            if not sfp:
                draw = rnd.randint(40, 255) if up else 0
                self.poe.append({"id": i, "mode": "Enabled", "priority": "Low", "power_limit_user": 30,
                                 "schedule": "Disabled", "_draw": draw, "_class": rnd.choice(["2", "3", "4"]) if up else "-"})
                if up:
                    self.devices[i] = rnd.choice(CAMERAS)
        self.counters = {p["id"]: [rnd.randint(10**6, 10**9), rnd.randint(10**6, 10**9), rnd.randint(2_000, 900_000)]
                         for p in self.ports}
        self.vlan = {"vlan": {"allowed_access_vlans": "1,10,20", "ethertype_custom_s_ports": "88a8"},
                     "ports": [{"id": p["id"], "vlan": {"mode": "Access", "access": {"pvid": 10 if p["id"] <= m["copper"] else 1, "forbidden_vlan": ""}}}
                               for p in self.ports]}
        self.ntp = {"automatic": True, "interval": 60, "server1": "ntp1.transition.com", "server2": "", "server3": "", "server4": "", "server5": ""}
        self.syslog = {"mode": False, "server_address": "", "server_port": 514}
        self.mirror = [{"destination_port": 0, "source_tx": "", "source_rx": ""}]
        self.ip = {"vid": 1, "ipv4": {"dhcp": False, "fallback": 0, "current_lease": "192.168.1.10/24",
                                      "static_addr": "192.168.1.10", "static_mask": 24}, "ipv6": {"static_addr": "", "static_mask": 0}}

    def tick(self):
        now = time.time()
        for p in self.ports:
            c = self.counters[p["id"]]
            if p["link"] != "down":
                c[0] += int(c[2] * random.uniform(0.6, 1.4))
                c[1] += int(c[2] * random.uniform(0.1, 0.5))
        if self.upgrade and now - self.upgrade["t"] > 20:
            self.upgrade = None
            self.fw = "B7.20.0215 2026-06-12"
            self.started = now

    def uptime(self):
        s = int(time.time() - self.started)
        return f"{s // 86400}d {s % 86400 // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def sispm_handler(state: SispmState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authed(self):
            ck = self.headers.get("Cookie", "")
            return any(part.strip() == f"seid={s}" for s in state.sessions for part in ck.split(";"))

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            if self.path == "/":
                data = b"<html><title>Transition Networks SISPM1040</title></html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.handle_api("GET", {})

        def do_POST(self):
            self.handle_api("POST", self.body())

        def handle_api(self, method, b):
            p = self.path
            if p == "/api/login" and method == "POST":
                lg = b.get("login", {})
                if lg.get("username") == "admin" and lg.get("password") in ("admin", "Lab-Passw0rd"):
                    state.sessions.add(str(lg.get("sessid")))
                    return self.reply({"response": {"status": "success", "message": ""}})
                return self.reply({"response": {"status": "fail", "message": "Invalid username or password"}})
            with state.lock:
                state.tick()
            if not self.authed():
                return self.reply({"response": {"status": "fail", "message": "Please login first"}}, 401)
            if state.upgrade and time.time() - state.upgrade["t"] > 8:
                self.close_connection = True  # rebooting
                return self.reply({"error": "rebooting"}, 503)
            with state.lock:
                state.tick()
                s = state
                if p == "/api/logout":
                    return self.reply({"response": {"status": "success"}})
                if p == "/api/get_sysinfo":
                    return self.reply({"system": {"information": dict(s.info, firmware_version=s.fw, uptime=s.uptime(),
                                       system_date=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                                       cpu_load=f"{random.randint(4, 30)}%, 6%, 5%",
                                       ram={"total": "43584 Kbytes", "free": f"{random.randint(18000, 24000)} Kbytes"},
                                       temperature_1=random.randint(44, 52), temperature_2=random.randint(58, 66))}})
                if p == "/api/set_sysinfo":
                    s.info.update(b["system"]["information"])
                    return self.reply(b)
                if p == "/api/get_port_config":
                    return self.reply({"ports": s.ports})
                if p == "/api/set_port_config":
                    for np in b.get("ports", []):
                        for op in s.ports:
                            if op["id"] == np["id"]:
                                was_down = op["speed_mode"] == "Disabled"
                                op.update({k: v for k, v in np.items() if k != "link"})
                                if op["speed_mode"] == "Disabled":
                                    op["link"] = "down"
                                elif was_down:
                                    op["link"] = "1Gfdx"
                    return self.reply({"ports": s.ports})
                if p == "/api/get_port_statistics":
                    return self.reply({"ports": [{"id": i, "statistics": {
                        "rx_packets": {"octets": c[0], "drop": 0, "crc_alignment": 0, "fragments": 0, "jabber": 0},
                        "tx_packets": {"octets": c[1], "drop": 0}}} for i, c in s.counters.items()]})
                if p == "/api/get_poe_status":
                    ports, used = [], 0
                    for e in s.poe:
                        port = s.ports[e["id"] - 1]
                        on = e["mode"] != "Disabled" and port["link"] != "down" and e["_draw"] > 0
                        draw = int(e["_draw"] * random.uniform(0.9, 1.1)) if on else 0
                        used += draw
                        ports.append({"id": e["id"], "poe": {"pd_class": e["_class"] if on else "-", "priority": e["priority"],
                                      "port_status": "PoE turned ON" if on else ("PoE turned OFF – PoE disabled" if e["mode"] == "Disabled" else "No PD detected"),
                                      "power_allocate": 300 if on else 0, "power_used": draw, "current_used": draw * 2}})
                    return self.reply({"poe": {"total_power_allocate": 0, "total_power_used": used, "total_current_used": used * 2}, "ports": ports})
                if p == "/api/get_poe_config":
                    return self.reply({"poe": {"total_power_watts": s.m["budget"], "power_determined_mode": "Class",
                                               "power_management_mode": "Actual Consumption", "capacitor_detection": False},
                                       "ports": [{"id": e["id"], "poe": {k: v for k, v in e.items() if not k.startswith("_") and k != "id"}} for e in s.poe]})
                if p == "/api/set_poe_config":
                    for np in b.get("ports", []):
                        for e in s.poe:
                            if e["id"] == np["id"]:
                                e.update(np["poe"])
                    return self.reply({"ok": True})
                if p == "/api/get_poe_auto_reset":
                    return self.reply({"poe": {"auto_checking": False}, "ports": [{"id": e["id"], "poe_auto_reset": {"ip": "0.0.0.0", "startup_time": 60, "interval_time": 30, "retry_time": 3, "error": 0, "total": 0, "failure_reboot": False, "reboot_time": 15, "max_reboot_times": 3}} for e in s.poe]})
                if p == "/api/set_poe_auto_reset":
                    return self.reply(b)
                if p == "/api/get_vlan_config":
                    return self.reply(s.vlan)
                if p == "/api/set_vlan_config":
                    s.vlan = copy.deepcopy(b)
                    return self.reply(b)
                if p == "/api/get_mac_based_vlan":
                    return self.reply({"vlan": {"mac_based_vlan": []}})
                if p == "/api/get_ip_address":
                    return self.reply({"system": {"ip": {"interfaces": [s.ip]}}})
                if p == "/api/get_mirror_config":
                    return self.reply({"system": {"mirror": s.mirror}})
                if p == "/api/set_mirror_config":
                    s.mirror = b["system"]["mirror"]
                    return self.reply(b)
                if p == "/api/get_ntp_server":
                    return self.reply({"system": {"ntp": s.ntp}})
                if p == "/api/set_ntp_server":
                    s.ntp = b["system"]["ntp"]
                    return self.reply(b)
                if p == "/api/get_syslog_server":
                    return self.reply({"system": {"syslog": s.syslog}})
                if p == "/api/set_syslog_server":
                    s.syslog = b["system"]["syslog"]
                    return self.reply(b)
                if p == "/api/get_system_time":
                    return self.reply({"system": {"time": {"clock_source": "Use NTP Server", "system_date": time.strftime("%Y-%m-%d %H:%M:%S"), "time_zone": "-3000", "acronym": "EST", "daylight": {"mode": "disable", "offset": 60}}}})
                if p == "/api/set_system_time":
                    return self.reply(b)
                if p == "/api/get_snmp_trap_config":
                    return self.reply({"snmp": {"trap_mode": False, "trap": []}})
                if p == "/api/get_account_config":
                    return self.reply({"account": [{"username": "admin", "privilege_level": 15}]})
                if p == "/api/get_acl_config":
                    return self.reply({"acl": {"ace": []}})
                if p == "/api/get_di_do_config":
                    return self.reply({"system": {"di_do": {"digital_out_mode": False, "di_normal_mode": "High", "di_normal_description": "", "di_abnormal_description": "", "do_normal_mode": "Open", "auto_recovery": False}}})
                if p == "/api/set_di_do_config":
                    return self.reply(b)
                if p == "/api/get_dynamic_mac_table":
                    return self.reply({"mac_table": [{"type": "Dynamic", "mac": f"00-40-8C-00-00-{i:02X}", "vid": 10, "port": i} for i in s.devices]})
                if p == "/api/dev_list_table":
                    return self.reply({"device_list_table": [{"switch_mac": s.info["mac_addr"], "switch_addr": "", "device_name": s.info["system_name"],
                                       "device_list": [{"port_no": i, "poe_used": 0, "status": "on", "device_type": "Camera" if "AP" not in name else "AP",
                                                        "model_name": name, "device_name": f"{name.split()[0]}-{i:02d}", "mac": f"00-40-8C-00-00-{i:02X}",
                                                        "ip_addr": f"10.10.{seed_octet(s)}.{100 + i}", "rx_rate": 1024, "link_partner_port_no": 0,
                                                        "number_of_alarm_events": 0, "events": []}
                                                       for i, name in s.devices.items() if s.ports[i - 1]["link"] != "down"]}]})
                if p == "/api/get_sfp_port_detail":
                    return self.reply({"ports": [{"id": str(pp["id"]), "sfp": {"vendor_name": "Lantronix", "vendor_pn": "TN-SFP-SXD", "bit_rate": "1 Gbps",
                                                  "tx_central_wavelength": "850", "temperature": f"{random.uniform(38, 47):.2f} C", "vcc": "3.31 V",
                                                  "mon2_tx_pwr": "-5.10 dBm", "mon3_rx_pwr": f"{random.uniform(-9, -6):.2f} dBm"}}
                                                 for pp in s.ports if pp["media"] == "fiber" and pp["link"] != "down"]})
                if p == "/api/save_configuration":
                    return self.reply({"response": {"status": "success", "message": "startup-config saved successfully."}})
                if p == "/api/reboot":
                    s.started = time.time()
                    return self.reply(None)
                if p == "/api/firmware_upgrade":
                    s.upgrade = {"t": time.time(), "url": b["system"]["firmware"]["upgrade_url"]}
                    return self.reply(None)
                if p == "/api/get_firmware_upgrade_status":
                    st = "idle" if not s.upgrade else "downloading"
                    return self.reply({"system": {"firmware": {"upgrade_status": st}}})
                if p == "/api/cable_diagnostics":
                    port = b["cable"]["port"]
                    return self.reply({"ports": [{"id": port, "cable_diagnostic": {"link": "1G", "result": "OK", "length": f"{random.randint(3, 90)}.00 (m)"}}]})
                if p == "/api/get_syslog":
                    return self.reply({"system": {"syslog": {"log": []}}})
                return self.reply({"response": {"status": "fail", "message": f"unknown endpoint {p}"}}, 404)
    return H


def seed_octet(s):
    return int(s.info["serial_number"][-2:]) % 200 + 20


def lss_handler(seed):
    rnd = random.Random(seed)
    token = f"eyJ.mock.{seed}"
    st = {"SYSNAME": f"LSS2200-{seed}", "LOCATION": "", "CONTACT": "", "started": time.time()}
    poe = {i: {"PORT": f"ge{i}", "POE-ENABLE": "enabled", "PRIORITY": "low", "_draw": rnd.choice([0, 7.2, 12.9, 25.5, 51.0])} for i in range(1, 9)}
    counters = {i: [rnd.randint(10**6, 10**9), rnd.randint(10**6, 10**9)] for i in range(1, 11)}
    paths = ["sysinfo/", "banner/", "users/", "date_time", "ports/", "poe/status/port/", "poe/config/port/",
             "lldp/neighbors/", "logical_ports", "config/save/", "reboot/"]
    swagger = {"openapi": "3.0.0", "paths": {"/" + p.rstrip("/"): ({"get": {}} if p not in ("config/save/", "reboot/") else {"post": {}}) for p in paths}}
    swagger["paths"]["/poe/config/port"]["post"] = {}
    swagger["paths"]["/sysinfo"]["post"] = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def route(self, method):
            p = self.path.split("?")[0].removeprefix("/rest/v1/").rstrip("/")
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if p == "" and method == "GET":
                return self.reply({"swagger_ui": True})
            if p == "login" and method == "POST":
                if (b.get("username") or b.get("USERNAME")) == "admin":
                    return self.reply({"access_token": token})
                return self.reply({"message": "Bad credentials"}, 401)
            if self.headers.get("Authorization") != f"Bearer {token}":
                return self.reply({"message": "Unauthorized"}, 401)
            for c in counters.values():
                c[0] += rnd.randint(10**5, 10**7)
                c[1] += rnd.randint(10**4, 10**6)
            if p == "swagger.json":
                return self.reply(swagger)
            if p == "sysinfo":
                if method == "POST":
                    st.update({k: v for k, v in b.items() if k in ("SYSNAME", "LOCATION", "CONTACT")})
                return self.reply({"MODEL": "LSS2200-8P", "SYSNAME": st["SYSNAME"], "LOCATION": st["LOCATION"],
                                   "CONTACT": st["CONTACT"], "FIRMWARE": "1.7.0.0R5", "MAC": f"00:80:a3:1e:{seed:02x}:01",
                                   "SERIAL": f"LS22{seed:06d}", "UPTIME": f"{int(time.time() - st['started'])}s",
                                   "CPU": rnd.randint(3, 20), "TEMPERATURE": rnd.randint(41, 49)})
            if p == "banner":
                return self.reply({"BANNER": "Authorized access only"})
            if p == "users":
                return self.reply([{"USERNAME": "admin", "GROUP": "admin", "ENABLED": "true"}])
            if p == "logical_ports":
                return self.reply([{"NAME": "mgmt", "DHCP": "true", "IGNORE_PEER_DNS": "false"}])
            if p == "ports":
                return self.reply([{"NAME": f"ge{i}" if i <= 8 else f"xe{i - 8}", "LINK": "up" if (i <= 8 and poe[i]["_draw"] or i == 9) else "down",
                                    "SPEED": "1G" if i <= 8 else "10G", "ADMIN": "up", "DESCRIPTION": "",
                                    "RX_BYTES": counters[i][0], "TX_BYTES": counters[i][1]} for i in range(1, 11)])
            if p == "poe/status/port":
                return self.reply([{"PORT": v["PORT"], "STATUS": "delivering" if v["_draw"] and v["POE-ENABLE"] != "disabled" else "searching",
                                    "POWER": v["_draw"] if v["POE-ENABLE"] != "disabled" else 0, "CLASS": "4" if v["_draw"] > 30 else "3"} for v in poe.values()])
            if p == "poe/config/port":
                if method == "POST":
                    i = int("".join(ch for ch in str(b.get("PORT")) if ch.isdigit()))
                    poe[i].update({k: v for k, v in b.items() if k in ("POE-ENABLE", "PRIORITY")})
                    return self.reply({"message": "ok"})
                return self.reply([{k: v for k, v in val.items() if not k.startswith("_")} for val in poe.values()])
            if p == "lldp/neighbors":
                return self.reply([{"PORT_NAME": "xe1", "SYSTEM_NAME": "core-sw", "SYSTEM_DESCRIPTION": "Core switch", "CHASSIS_ID": "00:11:22:33:44:55"}])
            if p == "date_time":
                return self.reply({"DATE": time.strftime("%Y-%m-%d"), "TIME": time.strftime("%H:%M:%S"), "SOURCE": "NTP"})
            if p in ("config/save", "reboot"):
                return self.reply({"message": "ok"})
            return self.reply({"message": "Not Found"}, 404)

        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")
    return H


def start(family, port, model="SISPM1040-582-LRT", seed=1, host="127.0.0.1"):
    handler = sispm_handler(SispmState(model, seed)) if family == "sispm" else lss_handler(seed)
    srv = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name=f"mock-{family}-{port}").start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="sispm", choices=["sispm", "lss"])
    ap.add_argument("--model", default="SISPM1040-582-LRT", choices=list(MODELS))
    ap.add_argument("--port", type=int, default=18081)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    start(a.family, a.port, a.model, a.seed, a.host)
    print(f"mock {a.family} {a.model} on {a.host}:{a.port}  (login admin/admin)")
    threading.Event().wait()
