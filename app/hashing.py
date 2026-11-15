"""Hash-Chain fuer GoBD-konforme Unveraenderlichkeit.

Jede Rechnung bekommt einen SHA-256-Hash ueber ihre normalisierten Inhalte.
Zusaetzlich wird der Hash der vorherigen Rechnung mit eingerechnet - so entsteht
eine Blockchain-aehnliche Kette. Jeder nachtraegliche Eingriff in eine aeltere
Rechnung bricht alle folgenden Chain-Hashes und ist damit erkennbar.
"""

import hashlib
import json
from decimal import Decimal
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"not serializable: {type(o)}")


def content_hash(payload: dict) -> str:
    """Kanonisches JSON -> SHA-256."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=_default).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def chain_hash(prev_hash: str, this_content_hash: str) -> str:
    return hashlib.sha256((prev_hash + this_content_hash).encode("utf-8")).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
