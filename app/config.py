"""Runtime configuration, all overridable by environment variables."""
import os

DATA_DIR = os.environ.get("SD_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
DB_PATH = os.path.join(DATA_DIR, "switchdeck.db")
FIRMWARE_DIR = os.path.join(DATA_DIR, "firmware")
SECRET_KEY_PATH = os.path.join(DATA_DIR, "secret.key")

HTTP_HOST = os.environ.get("SD_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("SD_HTTP_PORT", "8443"))

# Address the switches use to reach this server (for syslog target and firmware URLs).
PUBLIC_HOST = os.environ.get("SD_PUBLIC_HOST", "")

SYSLOG_PORT = int(os.environ.get("SD_SYSLOG_PORT", "5514"))
POLL_INTERVAL = int(os.environ.get("SD_POLL_INTERVAL", "30"))          # seconds
BACKUP_INTERVAL = int(os.environ.get("SD_BACKUP_INTERVAL", "86400"))   # seconds
DEVICE_TIMEOUT = float(os.environ.get("SD_DEVICE_TIMEOUT", "8"))
VERIFY_TLS = os.environ.get("SD_VERIFY_TLS", "0") == "1"               # switches ship self-signed certs

ADMIN_USER = os.environ.get("SD_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SD_ADMIN_PASSWORD", "")               # first-run only; generated if blank

DEMO = os.environ.get("SD_DEMO", "0") == "1"                          # start built-in simulated switches

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(FIRMWARE_DIR, exist_ok=True)
