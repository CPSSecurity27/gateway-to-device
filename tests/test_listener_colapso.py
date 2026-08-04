"""Cinco NOTIFY seguidos del mismo panel son UN solo trabajo: el fetch es por
MAC. Sin colapso, una ráfaga de comandos genera fetchs redundantes.
"""

from gtd.db.listener import CH_COMMANDS, CH_CONFIG, PgListener

MAC = "240AC4000110"


async def test_notify_repetido_colapsa():
    lis = PgListener("postgresql://x@localhost/x")
    for _ in range(5):
        lis._encolar(CH_COMMANDS, MAC)
    assert lis._q.qsize() == 1
    canal, mac = await lis.get()
    assert (canal, mac) == (CH_COMMANDS, MAC)
    # Tras el get, un NOTIFY nuevo del mismo panel vuelve a encolar.
    lis._encolar(CH_COMMANDS, MAC)
    assert lis._q.qsize() == 1


async def test_canales_distintos_no_se_colapsan_entre_si():
    lis = PgListener("postgresql://x@localhost/x")
    lis._encolar(CH_COMMANDS, MAC)
    lis._encolar(CH_CONFIG, MAC)     # mismo panel, otro canal: es otro trabajo
    assert lis._q.qsize() == 2
