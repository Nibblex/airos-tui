"""system.cfg as each web reads and writes it, and this simulator's password hashes
(with the password behind each, so that a backup applied to another device logs in)."""
from __future__ import annotations

import hashlib


def parse_cfg(text: str) -> dict[str, str]:
    out = {}
    for line in text.replace("\r", "").split("\n"):
        if "=" in line and line.strip():
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def cfg_text(cfg: dict[str, str]) -> str:
    return "".join(f"{k}={v}\n" for k, v in sorted(cfg.items()))

SIM_PASSWORDS: dict[str, str] = {}     # (each hash of sim_hash, and its password)


def sim_hash(password: str) -> str:
    """This simulator's password hash (as an MD5-crypt one looks, $1$…, with no salt); it
    remembers whose it is: once a backup of another simulated device is applied, its password
    logs in."""
    digest = "$1$simulated$" + hashlib.md5(password.encode()).hexdigest()[:22]
    SIM_PASSWORDS[digest] = password
    return digest
