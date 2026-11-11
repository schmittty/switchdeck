"""Break-glass administration from the host shell.

  docker exec -it switchdeck python -m app.cli users
  docker exec -it switchdeck python -m app.cli reset-password admin      # prompts; user must change it at next login
  docker exec -it switchdeck python -m app.cli create-admin rescue
  docker exec -it switchdeck python -m app.cli tacacs-off                # turn TACACS+ off if it locks everyone out
  docker exec -it switchdeck python -m app.cli unlock admin              # clear sessions; re-enable the account
"""
import getpass
import sys
import time

from . import db, security


def _pw(prompt="New password: "):
    while True:
        a = getpass.getpass(prompt)
        b = getpass.getpass("Again: ")
        if a != b:
            print("Passwords don't match.")
            continue
        try:
            security.password_policy(a)
        except security.LoginError as e:
            print(e)
            continue
        return a


def main(argv):
    db.init()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "users":
        for u in db.q("SELECT username, role, source, disabled, must_change, last_login FROM users ORDER BY username"):
            flags = ",".join(f for f, on in (("disabled", u["disabled"]), ("must-change", u["must_change"])) if on)
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login"])) if u["last_login"] else "never"
            print(f"{u['username']:<24} {u['role']:<9} {u['source']:<7} last={last} {flags}")
    elif cmd == "reset-password" and args:
        u = db.q1("SELECT * FROM users WHERE username=?", (args[0],))
        if not u or u["source"] != "local":
            print("no local user by that name")
            return 1
        db.x("UPDATE users SET pwhash=?, must_change=1, disabled=0 WHERE username=?", (security.hash_password(_pw()), args[0]))
        db.x("DELETE FROM sessions WHERE username=?", (args[0],))
        db.audit("cli", "reset_password", args[0])
        print(f"Password reset for {args[0]}; they'll choose a new one at sign-in.")
    elif cmd == "create-admin" and args:
        if db.q1("SELECT 1 FROM users WHERE username=?", (args[0],)):
            print("that username exists; use reset-password")
            return 1
        db.x("INSERT INTO users(username, pwhash, role, source, must_change, created) VALUES (?,?,'admin','local',1,?)",
             (args[0], security.hash_password(_pw()), time.time()))
        db.audit("cli", "create_admin", args[0])
        print(f"Local admin {args[0]} created.")
    elif cmd == "tacacs-off":
        cfg = security.tacacs_config()
        cfg["enabled"] = False
        db.set_setting("tacacs", cfg)
        db.audit("cli", "tacacs_off")
        print("TACACS+ disabled; local accounts work again.")
    elif cmd == "unlock" and args:
        db.x("UPDATE users SET disabled=0 WHERE username=?", (args[0],))
        db.x("DELETE FROM sessions WHERE username=?", (args[0],))
        db.audit("cli", "unlock", args[0])
        print(f"{args[0]} re-enabled. (Failed-login throttling clears itself after 5 minutes or a container restart.)")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
