"""Entry point: python -m app.main"""
import os
import signal
import sys

from . import config, db, security
from .services import manager, syslogd


def _demo():
    """Start simulated switches and register them so the UI has something to show."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools import mock_switch
    fleet = [("sispm", 18081, "SISPM1040-582-LRT", 1, "Lobby PoE++"),
             ("sispm", 18082, "SISPM1040-384-LRT-C", 2, "Parking Garage L2"),
             ("sispm", 18083, "SISPM1040-362-LRT", 3, "Gate House"),
             ("lss", 18084, None, 4, "Roof LSS2200")]
    for fam, port, model, seed, name in fleet:
        mock_switch.start(fam, port, model or "SISPM1040-582-LRT", seed)
        if not db.q1("SELECT 1 FROM devices WHERE host='127.0.0.1' AND port=?", (port,)):
            db.x("INSERT INTO devices(site_id, name, host, port, scheme, family, username, secret, created) "
                 "VALUES (1,?,?,?,?,?,?,?,strftime('%s','now'))",
                 (name, "127.0.0.1", port, "http", fam, "admin", security.encrypt("admin")))
    print("[demo] 4 simulated switches running on 127.0.0.1:18081-18084")


def main():
    db.init()
    pw = security.ensure_admin()
    if pw:
        print("=" * 64)
        if config.ADMIN_PASSWORD:
            print(f"  First run: admin account '{config.ADMIN_USER}' created with SD_ADMIN_PASSWORD.")
        else:
            print(f"  First run: default login is  {config.ADMIN_USER} / {pw}")
            print("  You'll be asked to choose a new password at first sign-in.")
        print("  Authentication guide: docs/AUTHENTICATION.md")
        print("=" * 64)
        with open(os.path.join(config.DATA_DIR, "initial-admin-password.txt"), "w") as f:
            f.write(f"{config.ADMIN_USER} / {pw if not config.ADMIN_PASSWORD else '(set via SD_ADMIN_PASSWORD)'}\n")
    if config.DEMO:
        _demo()
    syslogd.start(config.SYSLOG_PORT)
    manager.start()
    signal.signal(signal.SIGTERM, lambda *_: (manager.stop(), sys.exit(0)))
    from .server import serve
    serve()


if __name__ == "__main__":
    main()
