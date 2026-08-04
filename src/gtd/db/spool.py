"""Spool en disco del canal `up` — JSONL append-only.

aiomqtt ya ackeó (PUBACK) cuando el mensaje llega al handler: si Postgres está
caído, el mensaje no existe en ningún otro lado. status/tele son retained y
vuelven solos; una alarma no. Es el mismo agujero que el GtD le señaló al
firmware (doc 05 §6) — sería incoherente dejarlo abierto de este lado.

Síncrono a propósito: append es una línea + flush + fsync, y pasa como mucho
una vez por mensaje con la base caída. No amerita aiofiles.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("gtd.spool")


class Spool:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def append(self, entry: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def leer(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        entradas: list[dict[str, Any]] = []
        with self._path.open(encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    entradas.append(json.loads(linea))
                except json.JSONDecodeError:
                    # Una línea rota (corte a mitad de write) no puede frenar
                    # el drenado de las sanas.
                    log.error("línea corrupta en el spool, se descarta: %.80s", linea)
        return entradas

    def reescribir(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            self._path.unlink(missing_ok=True)
            return
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._path)
