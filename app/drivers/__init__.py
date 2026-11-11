from .base import AuthError, Driver, DriverError
from .lss import LssDriver
from .sispm import SispmDriver

FAMILIES = {
    "sispm": {"cls": SispmDriver, "label": "SISPM1040 series (unified API)", "default_port": 80,
              "models": ["SISPM1040-582-LRT", "SISPM1040-384-LRT-C", "SISPM1040-362-LRT"]},
    "lss": {"cls": LssDriver, "label": "LS Series (REST v1)", "default_port": 8000,
            "models": ["LSS2200-8P"]},
}


def family_for_model(model: str) -> str:
    m = (model or "").upper()
    return "lss" if m.startswith("LSS") or m.startswith("LS ") else "sispm"


def make_driver(device: dict, password: str) -> Driver:
    fam = FAMILIES.get(device["family"])
    if not fam:
        raise DriverError(f"unknown family {device['family']}")
    return fam["cls"](device["host"], device["username"], password,
                      port=device.get("port") or fam["default_port"],
                      scheme=device.get("scheme") or ("https" if device["family"] == "lss" else "http"))
