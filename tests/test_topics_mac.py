"""La MAC pelada (12 hex mayúsculas) es la clave en toda la base; AV- existe
solo en MQTT. La traducción vive en el borde (topics/contract) y en ningún
otro lado — doc 06 §1: el bug era falla silenciosa en las DOS direcciones.
"""

from gtd.domain import contract
from gtd.domain.contract import Channel
from gtd.mqtt import topics


def test_parse_devuelve_mac_pelada():
    assert topics.parse("av/AV-A842E38FCA6C/status") == ("A842E38FCA6C", Channel.STATUS)


def test_parse_rechaza_id_sin_prefijo():
    assert topics.parse("av/A842E38FCA6C/status") is None


def test_parse_rechaza_hex_invalido():
    assert topics.parse("av/AV-a842e38fca6c/status") is None   # minúsculas: no es del firmware
    assert topics.parse("av/AV-ZZ42E38FCA6C/status") is None
    assert topics.parse("av/AV-A842E38FCA/status") is None      # corta


def test_topicos_de_bajada_reponen_el_prefijo():
    assert topics.cmd_topic("A842E38FCA6C") == "av/AV-A842E38FCA6C/cmd"
    assert topics.cfg_topic("A842E38FCA6C") == "av/AV-A842E38FCA6C/cfg"


def test_helpers_de_contract():
    assert contract.mac_from_device_id("AV-A842E38FCA6C") == "A842E38FCA6C"
    assert contract.mac_from_device_id("A842E38FCA6C") is None
    assert contract.device_id_from_mac("A842E38FCA6C") == "AV-A842E38FCA6C"
