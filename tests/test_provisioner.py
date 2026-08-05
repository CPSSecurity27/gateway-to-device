"""Provisioner: drena la cola, invoca el script y confirma.

Sin base ni broker: la cola y el registrador se sustituyen por dobles. Lo que se
prueba es la POLÍTICA — el orden, el reload único por tanda, y que un fallo no
arrastre al resto.
"""

from gtd.provisioner.broker import RegistradorFalso
from gtd.provisioner.cola import ColaStub, Pendiente


# ── La cola ─────────────────────────────────────────────────────────

async def test_la_cola_stub_devuelve_lo_que_se_le_carga():
    cola = ColaStub([Pendiente(1, "AABBCCDDEE01", "provision")])
    assert await cola.pendientes() == [Pendiente(1, "AABBCCDDEE01", "provision")]


async def test_confirmar_saca_la_fila_de_pendientes():
    cola = ColaStub([Pendiente(1, "AABBCCDDEE01", "provision")])
    await cola.confirmar(1, "ok", None)
    assert await cola.pendientes() == []
    assert cola.confirmaciones == [(1, "ok", None)]


# ── El registrador ──────────────────────────────────────────────────

async def test_el_registrador_arma_los_argumentos_del_script():
    reg = RegistradorFalso()
    res, det = await reg.aplicar(Pendiente(1, "AABBCCDDEE01", "provision"))
    assert res == "ok" and det is None
    # En lote nunca se recarga por equipo ni se publica la prueba.
    assert reg.llamadas == [["AABBCCDDEE01", "--no-reload", "--no-probe"]]


async def test_revoke_pasa_el_subcomando_primero():
    reg = RegistradorFalso()
    await reg.aplicar(Pendiente(2, "AABBCCDDEE02", "revoke"))
    assert reg.llamadas == [["revoke", "AABBCCDDEE02", "--no-reload"]]


async def test_un_fallo_devuelve_error_con_el_detalle():
    reg = RegistradorFalso(falla_en={"AABBCCDDEE03"})
    res, det = await reg.aplicar(Pendiente(3, "AABBCCDDEE03", "provision"))
    assert res == "error"
    assert det and "vector" in det.lower()


# ── El drenaje ──────────────────────────────────────────────────────

async def test_drenar_procesa_todo_y_recarga_una_sola_vez():
    cola = ColaStub([
        Pendiente(1, "AABBCCDDEE01", "provision"),
        Pendiente(2, "AABBCCDDEE02", "provision"),
        Pendiente(3, "AABBCCDDEE03", "provision"),
    ])
    reg = RegistradorFalso()

    from gtd.provisioner.servicio import drenar
    hechos = await drenar(cola, reg)

    assert hechos == 3
    assert len(reg.llamadas) == 3
    # LO IMPORTANTE: un reload por tanda, no uno por equipo. Doscientos reload
    # seguidos sobre la Pi es una mala tarde.
    assert reg.recargas == 1
    assert [c[1] for c in cola.confirmaciones] == ["ok", "ok", "ok"]


async def test_un_fallo_no_arrastra_al_resto_de_la_tanda():
    cola = ColaStub([
        Pendiente(1, "AABBCCDDEE01", "provision"),
        Pendiente(2, "AABBCCDDEE02", "provision"),
        Pendiente(3, "AABBCCDDEE03", "provision"),
    ])
    reg = RegistradorFalso(falla_en={"AABBCCDDEE02"})

    from gtd.provisioner.servicio import drenar
    hechos = await drenar(cola, reg)

    assert hechos == 3
    resultados = {i: r for i, r, _ in cola.confirmaciones}
    assert resultados == {1: "ok", 2: "error", 3: "ok"}
    # Igual se recarga: los que SÍ salieron tienen que quedar activos.
    assert reg.recargas == 1


async def test_sin_pendientes_no_recarga():
    from gtd.provisioner.servicio import drenar
    cola = ColaStub([])
    reg = RegistradorFalso()
    assert await drenar(cola, reg) == 0
    assert reg.recargas == 0


async def test_el_orden_de_la_cola_se_respeta():
    """El fetch viene ordenado por created_at: el drenaje no lo puede alterar."""
    cola = ColaStub([
        Pendiente(10, "AABBCCDDEE0A", "provision"),
        Pendiente(11, "AABBCCDDEE0B", "revoke"),
        Pendiente(12, "AABBCCDDEE0C", "provision"),
    ])
    reg = RegistradorFalso()

    from gtd.provisioner.servicio import drenar
    await drenar(cola, reg)

    assert [c[0] for c in cola.confirmaciones] == [10, 11, 12]


# ── El aviso de la base ─────────────────────────────────────────────

async def test_el_bucle_espera_el_aviso_y_no_duerme_a_ciegas():
    """El alta de fábrica es SINCRÓNICA: la web espera 30 s.

    Si el bucle durmiera el intervalo entero sin escuchar el NOTIFY, toda
    fabricación se vencería y el equipo se borraría. Este test fija que el
    bucle pregunta por el aviso en vez de dormir.
    """
    import asyncio

    from gtd.provisioner.servicio import bucle

    class ColaQueAvisa(ColaStub):
        def __init__(self):
            super().__init__([])
            self.esperas: list[float] = []

        async def esperar_trabajo(self, timeout: float) -> bool:
            self.esperas.append(timeout)
            if len(self.esperas) >= 3:
                raise asyncio.CancelledError
            return True

    cola = ColaQueAvisa()
    with __import__("pytest").raises(asyncio.CancelledError):
        await bucle(cola, RegistradorFalso(), 20)

    assert cola.esperas == [20, 20, 20]


async def test_sin_base_esperar_trabajo_se_comporta_como_el_barrido():
    cola = ColaStub([])
    assert await cola.esperar_trabajo(0.01) is False
