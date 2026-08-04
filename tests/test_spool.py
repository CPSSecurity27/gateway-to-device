"""El spool es el WAL de los eventos: si Postgres está caído, el up ya está
ackeado en MQTT y NO puede perderse. JSONL append-only, drenado al recuperar.
"""

import json

from gtd.db.repo import RepoUnavailable, StubRepo
from gtd.db.spool import Spool
from gtd.pipeline import presencia, uplink

DEVICE_ID = "AV-240AC4000110"
MAC = "240AC4000110"


def test_append_y_leer_roundtrip(tmp_path):
    s = Spool(tmp_path / "up.jsonl")
    s.append({"mac": MAC, "doc": {"t": "alarma", "eid": "b-1"}})
    s.append({"mac": MAC, "doc": {"t": "ack", "cid": "c-9"}})
    leidos = s.leer()
    assert len(leidos) == 2 and leidos[0]["doc"]["eid"] == "b-1"
    s.reescribir([leidos[1]])
    assert len(s.leer()) == 1
    s.reescribir([])
    assert s.leer() == []


def test_leer_sin_archivo_devuelve_vacio(tmp_path):
    assert Spool(tmp_path / "no_existe.jsonl").leer() == []


def test_linea_corrupta_no_frena_el_drenado(tmp_path):
    ruta = tmp_path / "up.jsonl"
    s = Spool(ruta)
    s.append({"mac": MAC, "doc": {"t": "alarma", "eid": "b-1"}})
    with ruta.open("a", encoding="utf-8") as f:
        f.write('{"mac": "corta')   # corte a mitad de write
    s.append({"mac": MAC, "doc": {"t": "alarma", "eid": "b-2"}})
    leidos = s.leer()
    assert [e["doc"]["eid"] for e in leidos] == ["b-1", "b-2"]


class RepoCaido(StubRepo):
    async def insert_evento(self, *a, **kw):
        raise RepoUnavailable("base caída")


async def test_alarma_con_base_caida_va_al_spool(tmp_path):
    presencia.reiniciar()
    spool = Spool(tmp_path / "up.jsonl")
    alarma = json.dumps({"v": 1, "ts": 1, "tsq": 4, "t": "alarma", "eid": "b-2",
                         "mode": "alert", "prev": "off", "origin": "rf"}).encode()
    await uplink.handle(f"av/{DEVICE_ID}/up", alarma, RepoCaido(), spool=spool)
    guardado = spool.leer()
    assert guardado[0]["mac"] == MAC and guardado[0]["doc"]["eid"] == "b-2"


async def test_replay_reinserta(tmp_path):
    presencia.reiniciar()
    repo = StubRepo()
    await uplink.replay(MAC, {"v": 1, "t": "alarma", "eid": "b-3", "mode": "alert",
                              "prev": "off", "origin": "rf", "ts": 1, "tsq": 4}, repo)
    assert repo.eventos[0]["eid"] == "b-3"
