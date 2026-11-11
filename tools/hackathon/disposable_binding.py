"""Explicit, immutable identities of the two human-authorized disposable clusters.

Selection is explicit for PG18; absence retains the original PG16 binding.
Unknown selectors never fall back to another server.
"""

import os
from pathlib import PureWindowsPath
from urllib.parse import urlsplit

HOST = "127.0.0.1"
CLUSTER = os.environ.get("PAYPAL_DISPOSABLE_CLUSTER", "postgres16")
AUTHORIZED = {
    "postgres16": (55432, "7706080724031867336", "16.15", "disposable006-1e61a7bc34", "PG16_BIN", "006"),
    "postgres18": (55438, "7706108909131505256", "18.3", "disposable006-pg18-812e5e219b", "PG18_BIN", "006_pg18"),
}
if CLUSTER not in AUTHORIZED:
    raise RuntimeError("EXPLICIT_AUTHORIZED_DISPOSABLE_CLUSTER_REQUIRED")
PORT, SYSTEM, VERSION, DIRECTORY, BIN_KEY, DATABASE_PREFIX = AUTHORIZED[CLUSTER]
DATA = str(PureWindowsPath(os.environ.get("LOCALAPPDATA", "C:/__UNCONFIGURED_LOCALAPPDATA__"))
           / "InterlockAI" / DIRECTORY)
HEAD = "0029_unsent_terminal_guards"


def require_explicit_selection():
    # Defaults exist for offline/source inspection only. Every actual connection
    # boundary requires the caller to name its authorized cluster explicitly.
    if os.environ.get("PAYPAL_DISPOSABLE_CLUSTER") != CLUSTER:
        raise RuntimeError("EXPLICIT_AUTHORIZED_DISPOSABLE_CLUSTER_REQUIRED")


def checked_url(value, domain, role=None):
    p = urlsplit(value)
    name = p.path.removeprefix("/")
    if (p.scheme != "postgresql+psycopg" or p.hostname != HOST or p.port != PORT or p.query or p.fragment
            or domain not in {"control", "seller", "reference"} or not p.username or not p.password
            or not name.startswith("hackathon_disposable_") or not name.endswith("_" + domain)
            or (role is not None and p.username != role)):
        raise RuntimeError("AUTHORIZED_DISPOSABLE_URL_REQUIRED")
    return p


def checked_metadata(system, directory, version, port, listener):
    if (system != SYSTEM or PureWindowsPath(directory) != PureWindowsPath(DATA)
            or version != VERSION or str(port) != str(PORT) or listener != HOST):
        raise RuntimeError("AUTHORIZED_DISPOSABLE_CLUSTER_MISMATCH")
