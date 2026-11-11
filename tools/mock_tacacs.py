"""Tiny TACACS+ server for testing SwitchDeck logins without a real ISE / tac_plus.

  python -m tools.mock_tacacs --port 4949 --key testing123

Users (password = "<name>pass"): netadmin (priv-lvl 15), tech (priv-lvl 7), noc (priv-lvl 1),
roleuser (returns switchdeck-role=operator). Handles PAP and ASCII authentication and authorization.
"""
import argparse
import socketserver
import struct
import threading

from app.tacacs import (AUTHEN_FAIL, AUTHEN_GETPASS, AUTHEN_PASS, AUTHEN_TYPE_PAP, AUTHOR_FAIL,
                        AUTHOR_PASS_ADD, FLAG_UNENCRYPTED, TYPE_AUTHEN, TYPE_AUTHOR, _crypt)

USERS = {"netadmin": {"priv-lvl": "15"}, "tech": {"priv-lvl": "7"}, "noc": {"priv-lvl": "1"},
         "roleuser": {"switchdeck-role": "operator"}}


def make_handler(key: bytes):
    class H(socketserver.BaseRequestHandler):
        def read(self):
            hdr = self.request.recv(12)
            if len(hdr) < 12:
                return None
            ver, ptype, seq, flags, sid, ln = struct.unpack("!BBBBII", hdr)
            body = b""
            while len(body) < ln:
                body += self.request.recv(ln - len(body))
            if not flags & FLAG_UNENCRYPTED:
                body = _crypt(body, sid, key, ver, seq)
            return ver, ptype, seq, sid, body

        def write(self, ver, ptype, seq, sid, body):
            self.request.sendall(struct.pack("!BBBBII", ver, ptype, seq, 0, sid, len(body)) + _crypt(body, sid, key, ver, seq))

        def authen_reply(self, ver, seq, sid, status, msg=b""):
            self.write(ver, TYPE_AUTHEN, seq, sid, struct.pack("!BBHH", status, 0, len(msg), 0) + msg)

        def handle(self):
            pkt = self.read()
            if not pkt:
                return
            ver, ptype, seq, sid, body = pkt
            if ptype == TYPE_AUTHEN:
                _act, _priv, atype, _svc, ul, pl, rl, dl = struct.unpack("!8B", body[:8])
                user = body[8:8 + ul].decode(errors="replace")
                data = body[8 + ul + pl + rl:8 + ul + pl + rl + dl].decode(errors="replace")
                if atype != AUTHEN_TYPE_PAP:
                    self.authen_reply(ver, seq + 1, sid, AUTHEN_GETPASS, b"Password: ")
                    pkt = self.read()
                    if not pkt:
                        return
                    ver, _, seq, sid, body = pkt
                    ml, _dl, _fl = struct.unpack("!HHB", body[:5])
                    data = body[5:5 + ml].decode(errors="replace")
                ok = user in USERS and data == user + "pass"
                self.authen_reply(ver, seq + 1, sid, AUTHEN_PASS if ok else AUTHEN_FAIL, b"" if ok else b"Authentication failed")
            elif ptype == TYPE_AUTHOR:
                _m, _p, _t, _s, ul, pl, rl, ac = struct.unpack("!8B", body[:8])
                user = body[8 + ac:8 + ac + ul].decode(errors="replace")
                if user in USERS:
                    args = [f"{k}={v}".encode() for k, v in USERS[user].items()]
                    resp = struct.pack("!BBHH", AUTHOR_PASS_ADD, len(args), 0, 0) + bytes(len(a) for a in args) + b"".join(args)
                else:
                    resp = struct.pack("!BBHH", AUTHOR_FAIL, 0, 0, 0)
                self.write(ver, TYPE_AUTHOR, seq + 1, sid, resp)
    return H


def start(port=4949, key="testing123", host="127.0.0.1"):
    srv = socketserver.ThreadingTCPServer((host, port), make_handler(key.encode()))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4949)
    ap.add_argument("--key", default="testing123")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    start(a.port, a.key, a.host)
    print(f"mock TACACS+ on {a.host}:{a.port} key={a.key}; users: {', '.join(USERS)} (password = <user>pass)")
    threading.Event().wait()
