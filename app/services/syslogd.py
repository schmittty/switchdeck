"""UDP syslog receiver. Switches are pointed here during adoption; messages land in the event log."""
import re
import socketserver
import threading

from .. import db

SEVERITY = ["error", "error", "error", "error", "warning", "info", "info", "debug"]
PRI = re.compile(rb"^<(\d{1,3})>")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request[0][:4096]
        ip = self.client_address[0]
        level = "info"
        m = PRI.match(data)
        if m:
            level = SEVERITY[int(m.group(1)) & 7]
            data = data[m.end():]
        if level == "debug":
            return
        dev = db.q1("SELECT id FROM devices WHERE host=?", (ip,))
        db.event(dev["id"] if dev else None, level, data.decode(errors="replace").strip() or "(empty)",
                 f"syslog:{ip}")


def start(port):
    try:
        srv = socketserver.ThreadingUDPServer(("0.0.0.0", port), _Handler)
    except OSError as e:
        print(f"[syslog] could not bind UDP {port}: {e}")
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="syslog").start()
    print(f"[syslog] listening on UDP {port}")
    return srv
