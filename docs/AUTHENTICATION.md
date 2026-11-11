# Authentication guide

SwitchDeck handles two separate kinds of login:

```
 People  ──(1) sign in──▶  SwitchDeck  ──(2) API login──▶  Switches
 browser / dashboards        local accounts                 per-switch login, or
                             TACACS+                        a shared credential profile
                             API tokens (read-only)
```

1. **People signing in to SwitchDeck.** These are local accounts or TACACS+ accounts, each with a role. Dashboards such as Home Assistant use read-only API tokens instead.
2. **SwitchDeck signing in to each switch.** SwitchDeck uses a username and password stored encrypted on the server. Each switch can have its own login, or many switches can share one **credential profile**.

---

## 1. First sign-in

A fresh install creates one local account:

| Username | Password | |
|---|---|---|
| `admin` | `admin` | You must choose a new password before anything else works |

Browse to `http://<host>:8080`, sign in as `admin` / `admin`, and set a new password of at least 10 characters. Until you do, every API call except "change password" is refused. The startup log repeats the default, and so does `docker exec switchdeck cat /data/initial-admin-password.txt`.

To skip the default password, set `SD_ADMIN_PASSWORD` in `/opt/switchdeck/.env` **before the first start**. The admin account is then created with that password and no forced change. The variable only affects the first start; after that, manage passwords in the UI.

---

## 2. Roles

| Can… | viewer | operator | admin |
|---|:-:|:-:|:-:|
| See dashboard, devices, ports, topology, backups, firmware, events | ✓ | ✓ | ✓ |
| Power-cycle PoE, enable/disable ports, set descriptions, cable test | | ✓ | ✓ |
| Save config, take backups, poll now, set port uplink/access role | | ✓ | ✓ |
| Add/adopt/remove switches, reboot, restore backups, push firmware | | | ✓ |
| Settings, users, TACACS+, credential profiles, API tokens, audit log, API explorer | | | ✓ |

The server enforces these rules on every request; the UI simply hides buttons a role can't use. Every sign-in, failed sign-in and change is written to **Alerts & Events → Audit log**.

---

## 3. Local users

Manage local users under **Settings → Users** (admin only):

- **Add a local user.** Pick a role and set a temporary password. "Require a new password at first sign-in" is on by default.
- **Edit a local user** to change its role, reset its password (which forces a change at next sign-in), or disable it (which signs the user out immediately).
- **Keep break-glass access.** SwitchDeck refuses to delete, disable or demote the **last enabled local admin**. Keep that account even after TACACS+ is in use, for when the TACACS+ servers are down.

Password rules: at least 10 characters, and not a trivial value like `admin` or `password`. After 5 failed sign-ins for the same user from the same IP within 5 minutes, that user/IP is locked out for 5 minutes. Sessions last 12 hours.

---

## 4. TACACS+

With TACACS+, staff sign in with their normal network-admin accounts (for example AD accounts through Cisco ISE). The TACACS+ server decides each person's SwitchDeck role, and the server's own accounting shows who used SwitchDeck.

### 4.1 How a TACACS+ sign-in works
1. The user enters their username and password on the SwitchDeck sign-in page.
2. SwitchDeck sends a TACACS+ **authentication** request (PAP or ASCII) to the first configured server. If that server doesn't answer within the timeout, it tries the next one.
3. On success, SwitchDeck sends a TACACS+ **authorization** request for `service=shell` and reads the attributes in the reply:
   - `switchdeck-role=admin|operator|viewer` sets the role directly when present.
   - Otherwise `priv-lvl` decides: ≥ 15 → admin, ≥ 7 → operator, lower → viewer. Both thresholds are configurable.
   - If neither attribute is returned, the user gets the **default role** (viewer).
4. The user appears under **Settings → Users** with source "TACACS+". Their role is refreshed at every sign-in. Passwords are never stored in SwitchDeck.

### 4.2 Configure SwitchDeck
Go to **Settings → TACACS+ authentication**:

| Field | What to enter |
|---|---|
| Servers | One per line, in order: `10.1.1.10` or `10.1.1.10:49` |
| Shared key | The same secret configured for SwitchDeck on the TACACS+ server |
| Login type | **PAP** (most servers, including ISE) or **ASCII** (try this if PAP is rejected) |
| Timeout | Seconds to wait per server before trying the next (default 5) |
| Admin / Operator at priv-lvl ≥ | Thresholds for role mapping (defaults 15 and 7) |
| Role if no priv-lvl | Role when the server returns no level (default viewer) |
| Authorization service | Normally `shell`. Change it only if your server uses a custom service. |
| Local accounts | **Always allowed** (local accounts are checked first, then TACACS+), or **Only when TACACS+ is unreachable** (TACACS+ is authoritative, and local accounts work only when no server answers) |

**Before turning it on**, click **Test a login…** with a real TACACS+ admin account. The test uses the values in the form, so nothing has to be saved yet. It shows whether the login was accepted or rejected, which server answered, the attributes returned, and the SwitchDeck role the user would get. Then tick **Allow sign-in with TACACS+ accounts** and click **Save**.

> Firewall: SwitchDeck connects **out** to each TACACS+ server on TCP **49**. The TACACS+ server sees the request coming from the SwitchDeck host's IP address.

### 4.3 Cisco ISE (Device Administration)
Menu names follow ISE 3.x.

1. **Enable the Device Admin Service** on a Policy Service node: Administration → System → Deployment → *(node)* → *Enable Device Admin Service*.
2. **Add SwitchDeck as a network device:** Work Centers → Device Administration → Network Resources → Network Devices → **Add**.
   - Name `SwitchDeck`, IP address = the SwitchDeck host.
   - Tick **TACACS+ Authentication Settings** and enter the **Shared Secret** (the same key you put in SwitchDeck).
   - Optional: create a Network Device Group such as *Device Type → SwitchDeck* for the policy below.
3. **Create TACACS profiles:** Work Centers → Device Administration → Policy Elements → Results → **TACACS Profiles** → Add.
   - `SwitchDeck-Admin`: Common Task Type *Shell*, Default Privilege **15**, Maximum Privilege **15**.
   - `SwitchDeck-Operator`: Default/Maximum Privilege **7**.
   - `SwitchDeck-Viewer`: Default/Maximum Privilege **1**.
   - *Alternative:* add a custom attribute `switchdeck-role` = `admin` / `operator` / `viewer` (type Mandatory) instead of using privilege levels.
4. **Create a Device Admin Policy Set:** Work Centers → Device Administration → Device Admin Policy Sets.
   - Condition: *Network Device Group = SwitchDeck* (or *Device IP = SwitchDeck host*).
   - Allowed protocols: *Default Device Admin*.
   - **Authentication policy:** your identity source, for example Active Directory.
   - **Authorization policy:** one rule per AD group. For example *AD Group = NetAdmins → Shell Profile SwitchDeck-Admin*, *NOC → SwitchDeck-Viewer*, and so on. Leave the default rule as *Deny All Shell Profile*.
5. In SwitchDeck, set **Login type = PAP** and run **Test a login…**. Check Operations → TACACS → Live Logs in ISE if anything fails.

### 4.4 tac_plus (shrubbery / tac_plus-F)
```
key = "use-a-long-random-shared-key"

group = switchdeck-admin    { service = shell { priv-lvl = 15 } }
group = switchdeck-operator { service = shell { priv-lvl = 7 } }
group = switchdeck-viewer   { service = shell { priv-lvl = 1 } }

user = jdoe     { login = PAM  member = switchdeck-admin }
user = nocdesk  { login = PAM  member = switchdeck-viewer }
```
To set the role explicitly instead, add `switchdeck-role = operator` inside the `service = shell { }` block.

### 4.5 Other servers (ClearPass, tac_plus-ng, FortiAuthenticator, …)
Any RFC 8907 server works. Configure:
- a client entry for the SwitchDeck host IP with the shared key;
- PAP (or ASCII) login;
- a shell authorization result returning `priv-lvl`, or a `switchdeck-role` attribute.

### 4.6 Recommended setup
- Keep one or two **local admin** accounts with long passwords, stored in your password vault, as break-glass access.
- Set **Local accounts = Only when TACACS+ is unreachable** once TACACS+ logins are proven. Day-to-day sign-ins then always go through TACACS+, and the local admin still works during a TACACS+ outage.
- Give the NOC and help desk viewer access, and give field techs operator access so they can bounce camera ports.

### 4.7 Troubleshooting

| Symptom (Test a login… result) | Likely cause |
|---|---|
| `UNREACHABLE: … Connection refused / timed out` | Wrong IP or port, a firewall blocking TCP 49, or the TACACS+ service not running (on ISE: Device Admin Service not enabled) |
| `ERROR: garbled authentication reply (wrong shared secret?)` | The shared key differs between SwitchDeck and the server |
| `ERROR: server closed the connection` | The server doesn't recognise SwitchDeck as a client: wrong source IP, or no network device entry |
| `REJECTED` | Bad username/password, or the server's authentication policy denied the login. Check the server's live log |
| Accepted but role is `viewer` | No `priv-lvl` or `switchdeck-role` came back in authorization. Check the shell profile, or set *Role if no priv-lvl* |
| Works with PAP but not ASCII, or the reverse | Switch **Login type** to whichever one your server's policy allows |
| Login page says "authentication servers unreachable" | TACACS+ is authoritative and down, and the user has no local account. Sign in with the local break-glass admin |

---

## 5. Switch credentials (SwitchDeck → switches)

SwitchDeck logs in to each switch's API (SISPM1040 `/api/login`, or LS Series REST) with stored credentials. Passwords are encrypted at rest with the key in `/data/secret.key`.

**Option A: per-switch login.** Enter a username and password when adding the switch. Edit them later in the device drawer under **Settings**.

**Option B: credential profile (recommended for fleets).** Set it up under **Settings → Switch credential profiles → Profile**: name it (for example *Switch service account*) and enter the username and password. Then:
- When adding a switch, choose **Switch login → Profile: …**. Profiles are pre-selected when they exist.
- To move existing switches onto a profile, open each switch → **Settings → Switch login**.
- To rotate the password, change it on the switches, then edit the profile once. Every switch using it picks up the new password on its next poll.

**Recommendations**
- Create a **dedicated service account** on the switches (for example `switchdeck`) with full admin privilege (SISPM privilege level 15). The API needs admin rights for configuration, backups and PoE control. Don't share it with people.
- **If the switches authenticate their own logins through TACACS+/RADIUS**, the service account must exist where the switch looks it up. Either create it on the TACACS+ server with a privilege-15 shell profile, or keep it as a local account on each switch if the switch falls back to local users. The SwitchDeck **Poll** button (or the device going online) confirms the login works.
- Use HTTPS to the switches where the firmware supports it (SISPM1040-582-LRT B7.20.0191+, LS Series always). Set Scheme = https and the matching port on the device.
- A wrong or expired switch password shows the switch as **offline** with `login rejected` in its status and an event in **Alerts & Events**.

---

## 6. API tokens (dashboards, Home Assistant)

**Settings → Integrations → New API token** creates a read-only bearer token:
- It can read **only** `/api/v1/summary`, `/api/v1/devices` and `/api/v1/events`. It cannot change anything or see credentials.
- It is shown once and stored as a hash. Revoking it takes effect immediately.
- Send it as `Authorization: Bearer sdk_…`. The UI provides the exact Home Assistant `rest:` YAML.

---

## 7. Securing the web UI
- **HTTPS:** put a certificate and key in the data volume and set `SD_TLS_CERT=/data/tls/cert.pem` and `SD_TLS_KEY=/data/tls/key.pem` in `.env`, then run `docker compose up -d`. The firmware port (8081) stays plain HTTP because switches download images from it; it serves only unguessable file URLs.
- **Cookies:** sessions are HttpOnly and SameSite=Strict, and state-changing calls require a custom header (CSRF protection).
- **Network:** restrict who can reach port 8080 with your firewall or management VLAN ACLs.

---

## 8. Recovery (break-glass from the host)
Run these on the Docker host:
```bash
docker exec -it switchdeck python -m app.cli users                    # list accounts, roles, last login
docker exec -it switchdeck python -m app.cli reset-password admin     # set a new password (forced change at sign-in)
docker exec -it switchdeck python -m app.cli create-admin rescue      # add another local admin
docker exec -it switchdeck python -m app.cli tacacs-off               # disable TACACS+ if it locks everyone out
docker exec -it switchdeck python -m app.cli unlock jdoe              # re-enable a disabled account, end its sessions
```
Each command is recorded in the audit log as user `cli`. A lockout from failed sign-ins clears itself after 5 minutes, or immediately on `docker restart switchdeck`.
