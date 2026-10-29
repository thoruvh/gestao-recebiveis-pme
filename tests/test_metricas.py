"""Testes do pipeline e das métricas, rodando sobre uma base pequena gerada na hora."""
import duckdb
import numpy as np
import pandas as pd
import pytest

from recebiveis import config, etl, gerar_dados
from recebiveis import metricas as m


@pytest.fixture(scope="session")
def con(tmp_path_factory, monkeypatch_session):
    base = tmp_path_factory.mktemp("dados")
    monkeypatch_session.setattr(config, "BRONZE", base / "bronze")
    monkeypatch_session.setattr(config, "GOLD", base / "gold")
    monkeypatch_session.setattr(config, "BANCO", base / "teste.duckdb")
    for mod in (etl, gerar_dados):
        for nome in ("BRONZE", "GOLD", "BANCO"):
            if hasattr(mod, nome):
                monkeypatch_session.setattr(mod, nome, getattr(config, nome))
    gerar_dados.salvar(gerar_dados.gerar(n_clientes=300, meses=18, seed=7))
    etl.rodar()
    c = duckdb.connect(str(config.BANCO), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="session")
def monkeypatch_session():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_faixas_de_atraso_corretas(con):
    df = con.execute("SELECT dias_atraso, faixa FROM fato_snapshot").df()
    limites = {"A vencer": (0, 0), "1-30": (1, 30), "31-60": (31, 60), "61-90": (61, 90),
               "91-180": (91, 180), "180+": (181, 360)}
    for faixa, (lo, hi) in limites.items():
        d = df[df.faixa == faixa].dias_atraso
        assert d.between(lo, hi).all(), faixa


def test_titulo_liquidado_sai_da_carteira(con):
    n = con.execute("""SELECT COUNT(*) FROM fato_snapshot s JOIN fato_titulo t USING (titulo_id)
                       WHERE t.data_pagamento <= s.data_ref""").fetchone()[0]
    assert n == 0


def test_roll_rate_linhas_somam_um(con):
    mat = m.matriz_roll_rate(con)
    somas = mat.sum(axis=1)
    assert np.allclose(somas[somas > 0], 1)


def test_aging_bate_com_snapshot(con):
    ag = m.aging(con).groupby("data_ref").saldo.sum()
    snap = con.execute("SELECT data_ref, SUM(valor) FROM fato_snapshot GROUP BY 1").df().set_index("data_ref").iloc[:, 0]
    assert np.allclose(ag.sort_index().values, snap.sort_index().values)


def test_perda_esperada_coerente(con):
    pe = m.perda_esperada(con)
    assert pe.prob_perda.between(0, 1).all()
    # quanto mais atrasado, maior a probabilidade de perda
    assert pe.prob_perda.is_monotonic_increasing
    assert (pe.perda_esperada <= pe.saldo + 1e-6).all()


def test_safras_ever90_nao_diminui(con):
    sf = m.safras(con)
    for _, g in sf.groupby("safra"):
        assert g.sort_values("mob").pct_ever90.is_monotonic_increasing


def test_previsao_nao_excede_carteira(con):
    fc = m.previsao_caixa(con)
    assert len(fc) == 13
    assert (fc.previsto >= 0).all()
    assert fc.previsto.sum() <= fc.attrs["carteira_aberta"] + 1e-6


def test_backtest_sem_vazamento_e_razoavel(con):
    bt = m.backtest_caixa(con)
    assert bt.attrs["data_corte"] < pd.Timestamp(config.DATA_CORTE)
    erro = abs(bt.previsto.sum() / bt.realizado.sum() - 1)
    assert erro < 0.25, f"erro do backtest muito alto: {erro:.1%}"


def test_filtro_reduz_carteira(con):
    total = m.kpis(con)["carteira"]
    so_varejo = m.kpis(con, m.Filtros(segmentos=["Varejo"]))["carteira"]
    assert 0 < so_varejo < total
