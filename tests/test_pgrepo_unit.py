"""Lo de PgRepo que se puede afirmar sin un Postgres: la forma del SQL
(notación nombrada — desacopla del orden de la firma, P2-5) y la política de
reintentos con RepoUnavailable. La integración real está en
test_pg_integracion.py, gated por GTD_TEST_PG_DSN.
"""

import pytest

from gtd.db.repo import PgRepo, RepoUnavailable

MAC = "240AC4000110"


def test_el_sql_usa_notacion_nombrada():
    assert "p_mac =>" in PgRepo._SQL_UPSERT_STATE
    assert "p_seen =>" in PgRepo._SQL_UPSERT_STATE
    assert "p_eid =>" in PgRepo._SQL_INSERT_EVENTO
    assert "p_det =>" in PgRepo._SQL_MARK_CFG_FAILED


class _PoolCaido:
    """Un pool cuyo acquire siempre revienta con error de conexión."""

    def acquire(self):
        raise ConnectionError("base caída (test)")


async def test_insert_evento_agota_reintentos_y_avisa():
    repo = PgRepo("postgresql://nadie@127.0.0.1:1/no_existe")
    repo.RETRY_BASE_S = 0.001          # que el test no espere de verdad
    repo._pool = _PoolCaido()
    with pytest.raises(RepoUnavailable):
        await repo.insert_evento(MAC, "alarma", {"mode": "alert"}, eid="e-1")


async def test_confirm_command_tambien_es_acotado():
    repo = PgRepo("postgresql://nadie@127.0.0.1:1/no_existe")
    repo.RETRY_BASE_S = 0.001
    repo._pool = _PoolCaido()
    with pytest.raises(RepoUnavailable):
        await repo.confirm_command("c-1", res="ok")
