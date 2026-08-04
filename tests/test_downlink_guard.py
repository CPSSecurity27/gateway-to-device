"""La cfg que no entra en el buffer del panel NO se publica: se marca failed
con el porqué. Publicarla sería un no-op silencioso del firmware; mark_sent
sería mentira (P0-2). Y todo JSON de bajada va compacto: cada byte acerca el
límite de MQTT_IN_PAYLOAD_MAX.
"""

from gtd.db.listener import CH_COMMANDS, CH_CONFIG
from gtd.db.repo import StubRepo
from gtd.domain.contract import MQTT_IN_PAYLOAD_MAX
from gtd.pipeline import downlink

MAC = "240AC4000110"


class RepoFalso(StubRepo):
    def __init__(self, cfg=None, cmds=()):
        super().__init__()
        self._cfg, self._cmds = cfg, list(cmds)
        self.failed = []
        self.sent = []

    async def fetch_pending_config(self, mac):
        return self._cfg

    async def fetch_pending_commands(self, mac):
        return self._cmds

    async def mark_config_failed(self, mac, cfg_v, det):
        self.failed.append((mac, cfg_v, det))

    async def mark_config_sent(self, mac, cfg_v):
        self.sent.append((mac, cfg_v))


class PubEspia:
    def __init__(self):
        self.publicados = []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.publicados.append((topic, payload, retain))


async def test_cfg_gigante_se_marca_failed_y_no_se_publica():
    gorda = {"cfg_v": 9, "redes": [{"ssid": f"red{i}", "psw": "x" * 60} for i in range(12)]}
    repo, pub = RepoFalso(cfg={"cfg_v": 9, "payload": gorda}), PubEspia()
    await downlink.handle(CH_CONFIG, MAC, repo, pub)
    assert pub.publicados == []
    assert repo.sent == []
    assert len(repo.failed) == 1
    mac, cfg_v, det = repo.failed[0]
    assert (mac, cfg_v) == (MAC, 9) and str(MQTT_IN_PAYLOAD_MAX) in det


async def test_cfg_normal_sale_compacta_y_retenida():
    repo = RepoFalso(cfg={"cfg_v": 3, "payload": {"cfg_v": 3, "modulos": {"rf": True}}})
    pub = PubEspia()
    await downlink.handle(CH_CONFIG, MAC, repo, pub)
    topic, payload, retain = pub.publicados[0]
    assert topic == "av/AV-240AC4000110/cfg" and retain
    assert " " not in payload            # separadores compactos
    assert repo.sent == [(MAC, 3)]


async def test_comando_sale_compacto_y_sin_retain():
    cmd = {"cid": "c-1", "tipo": "estado", "payload": {"v": 1, "t": "estado", "cid": "c-1"}}
    repo, pub = RepoFalso(cmds=[cmd]), PubEspia()
    await downlink.handle(CH_COMMANDS, MAC, repo, pub)
    topic, payload, retain = pub.publicados[0]
    assert topic == "av/AV-240AC4000110/cmd" and not retain
    assert " " not in payload
