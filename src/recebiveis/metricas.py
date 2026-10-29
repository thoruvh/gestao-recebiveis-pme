"""Métricas de crédito sobre a camada gold.

Todas as funções aceitam filtros opcionais por segmento, porte e faixa de score,
para que o dashboard (ou um notebook) recorte a carteira sem reprocessar o ETL.
"""
from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from recebiveis.config import FAIXAS

ABSORVENTES = ["Liquidado", "Baixa (perda)"]


@dataclass
class Filtros:
    segmentos: list[str] | None = None
    portes: list[str] | None = None
    faixas_score: list[str] | None = None

    def where(self, alias: str = "") -> tuple[str, list]:
        p = f"{alias}." if alias else ""
        cond, params = ["1=1"], []
        for col, vals in (("segmento", self.segmentos), ("porte", self.portes),
                          ("faixa_score", self.faixas_score)):
            if vals:
                cond.append(f"{p}{col} IN ({', '.join('?' * len(vals))})")
                params += list(vals)
        return " AND ".join(cond), params


TODOS = Filtros()  # sem recorte


def _q(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pd.DataFrame:
    return con.execute(sql, params or []).df()


# --------------------------------------------------------------------------- KPIs
def kpis(con, f: Filtros = TODOS, data_ref: str | None = None) -> dict:
    w, p = f.where()
    ref = data_ref or con.execute("SELECT MAX(data_ref) FROM dim_calendario").fetchone()[0]
    ag = _q(con, f"SELECT faixa, SUM(saldo) saldo FROM gold_aging WHERE data_ref = ? AND {w} GROUP BY 1",
            [ref] + p).set_index("faixa").saldo.reindex(FAIXAS, fill_value=0)
    total = float(ag.sum())
    fat90 = con.execute(
        f"SELECT SUM(faturado) FROM gold_fluxo_mensal WHERE mes > CAST(? AS DATE) - INTERVAL 3 MONTH "
        f"AND mes <= ? AND {w}", [ref, ref] + p).fetchone()[0] or 0
    vencido = total - float(ag["A vencer"])
    over90 = float(ag[["91-180", "180+"]].sum())
    return {
        "data_ref": pd.Timestamp(ref),
        "carteira": total,
        "vencido": vencido,
        "pct_vencido": vencido / total if total else 0,
        "over90": over90,
        "pct_over90": over90 / total if total else 0,
        "dso": total / float(fat90) * 90 if fat90 else np.nan,
    }


# --------------------------------------------------------------------------- Aging
def aging(con, f: Filtros = TODOS) -> pd.DataFrame:
    w, p = f.where()
    df = _q(con, f"SELECT data_ref, faixa, SUM(saldo) saldo, SUM(qtd_titulos) qtd "
                 f"FROM gold_aging WHERE {w} GROUP BY 1, 2 ORDER BY 1", p)
    df["faixa"] = pd.Categorical(df.faixa, FAIXAS, ordered=True)
    return df.sort_values(["data_ref", "faixa"])


# --------------------------------------------------------------------------- Roll rate
def matriz_roll_rate(con, f: Filtros = TODOS, meses: int = 6) -> pd.DataFrame:
    """Matriz de transição média (ponderada por saldo) dos últimos `meses` fechamentos."""
    w, p = f.where()
    df = _q(con, f"""
        SELECT faixa_origem, faixa_destino, SUM(saldo) saldo
        FROM gold_roll_rate
        WHERE data_ref > (SELECT MAX(data_ref) FROM gold_roll_rate) - INTERVAL {int(meses)} MONTH
          AND {w}
        GROUP BY 1, 2""", p)
    m = df.pivot_table(index="faixa_origem", columns="faixa_destino", values="saldo",
                       aggfunc="sum", fill_value=0)
    m = m.reindex(index=FAIXAS, columns=FAIXAS + ABSORVENTES, fill_value=0)
    return m.div(m.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def evolucao_roll_rate(con, f: Filtros = TODOS) -> pd.DataFrame:
    """% do saldo de cada faixa que piorou para a faixa seguinte, mês a mês (roll forward)."""
    w, p = f.where()
    df = _q(con, f"SELECT data_ref, faixa_origem, faixa_destino, SUM(saldo) saldo "
                 f"FROM gold_roll_rate WHERE {w} GROUP BY ALL", p)
    seguinte = dict(zip(FAIXAS[:-1], FAIXAS[1:], strict=True)) | {"180+": "Baixa (perda)"}
    df["piorou"] = df.apply(lambda r: r.faixa_destino == seguinte[r.faixa_origem], axis=1)
    g = df.groupby(["data_ref", "faixa_origem"]).apply(
        lambda d: d.loc[d.piorou, "saldo"].sum() / d.saldo.sum(), include_groups=False)
    out = g.rename("roll_forward").reset_index()
    out["transicao"] = out.faixa_origem.map(lambda o: f"{o} → {seguinte[o]}")
    return out[out.faixa_origem != "A vencer"]


# --------------------------------------------------------------------------- Perda esperada
def perda_esperada(con, f: Filtros = TODOS, meses: int = 6) -> pd.DataFrame:
    """Probabilidade de cada faixa terminar em baixa, via cadeia de Markov absorvente.

    Q = transições entre faixas em aberto; R = transições para estados finais.
    N = (I - Q)^-1 é a matriz fundamental; B = N @ R dá a probabilidade de absorção.
    Como a recuperação antes da baixa já aparece como 'Liquidado', LGD = 100% na baixa.
    """
    m = matriz_roll_rate(con, f, meses)
    Q = m[FAIXAS].values
    R = m[ABSORVENTES].values
    B = np.linalg.solve(np.eye(len(FAIXAS)) - Q, R)
    pd_baixa = pd.Series(B[:, ABSORVENTES.index("Baixa (perda)")], index=FAIXAS).clip(0, 1)

    ref = con.execute("SELECT MAX(data_ref) FROM dim_calendario").fetchone()[0]
    w, p = f.where()
    saldo = _q(con, f"SELECT faixa, SUM(saldo) saldo FROM gold_aging WHERE data_ref = ? AND {w} GROUP BY 1",
               [ref] + p).set_index("faixa").saldo.reindex(FAIXAS, fill_value=0)
    out = pd.DataFrame({"faixa": FAIXAS, "saldo": saldo.values, "prob_perda": pd_baixa.values})
    out["perda_esperada"] = out.saldo * out.prob_perda
    return out


# --------------------------------------------------------------------------- Safras
def safras(con, f: Filtros = TODOS) -> pd.DataFrame:
    w, p = f.where()
    df = _q(con, f"SELECT safra, mob, SUM(valor_emitido) emitido, SUM(valor_ever90) ever90 "
                 f"FROM gold_safras WHERE {w} GROUP BY 1, 2 ORDER BY 1, 2", p)
    df["pct_ever90"] = df.ever90 / df.emitido
    return df


# --------------------------------------------------------------------------- Concentração
def concentracao(con, f: Filtros = TODOS, top: int = 15) -> pd.DataFrame:
    w, p = f.where("c")
    return _q(con, f"""
        SELECT c.cliente_id, c.razao_social, c.segmento, c.porte, c.faixa_score,
               SUM(s.valor) saldo,
               SUM(CASE WHEN s.dias_atraso > 0 THEN s.valor ELSE 0 END) vencido,
               MAX(s.dias_atraso) maior_atraso
        FROM fato_snapshot s JOIN dim_cliente c USING (cliente_id)
        WHERE s.data_ref = (SELECT MAX(data_ref) FROM dim_calendario) AND {w}
        GROUP BY ALL ORDER BY saldo DESC LIMIT {int(top)}""", p)


# --------------------------------------------------------------------------- Previsão de caixa
def previsao_caixa(con, f: Filtros = TODOS, data_corte: str | None = None,
                   semanas: int = 13, maturacao_dias: int = 180) -> pd.DataFrame:
    """Projeta, semana a semana, quanto da carteira em aberto deve virar caixa.

    Para cada faixa de score, estima a distribuição empírica de 'dias de atraso no
    pagamento' (incluindo a chance de nunca pagar) a partir de títulos já maduros.
    Cada título em aberto recebe essa distribuição *condicionada* ao atraso que já
    acumulou, ou seja, um título com 40 dias de atraso usa P(pagar em x | x > 40).
    Só usa informação disponível até `data_corte`, o que permite fazer backtest.
    """
    corte = pd.Timestamp(data_corte or con.execute("SELECT MAX(data_ref) FROM dim_calendario").fetchone()[0])
    w, p = f.where("c")
    t = _q(con, f"""SELECT t.titulo_id, t.valor, t.data_emissao, t.data_vencimento, t.data_pagamento,
                          c.faixa_score
                   FROM fato_titulo t JOIN dim_cliente c USING (cliente_id) WHERE {w}""", p)
    for col in ("data_emissao", "data_vencimento", "data_pagamento"):
        t[col] = pd.to_datetime(t[col])
    t["data_pagamento"] = t.data_pagamento.where(t.data_pagamento <= corte)  # não olhar o futuro

    lo, hi = -10, 360
    eixo = np.arange(lo, hi + 1)
    hist = t[t.data_vencimento <= corte - pd.Timedelta(days=maturacao_dias)]
    pmfs = {}
    for faixa, g in hist.groupby("faixa_score"):
        d = (g.data_pagamento - g.data_vencimento).dt.days.clip(lo, hi)
        pesos = g.valor.values
        pmf = np.bincount((d.dropna() - lo).astype(int), weights=pesos[d.notna().values],
                          minlength=len(eixo)).astype(float)
        nunca = pesos[d.isna().values].sum()
        tot = pmf.sum() + nunca
        pmfs[faixa] = (pmf / tot, nunca / tot)

    aberto = t[(t.data_emissao <= corte) & t.data_pagamento.isna()
               & ((corte - t.data_vencimento).dt.days <= 360)].copy()
    aberto["obs"] = (corte - aberto.data_vencimento).dt.days
    fim = corte + pd.Timedelta(weeks=semanas)
    esperado = pd.Series(0.0, index=pd.date_range(corte + pd.Timedelta(days=1), fim))

    for (faixa, obs), g in aberto.groupby(["faixa_score", "obs"]):
        if faixa not in pmfs:
            continue
        pmf, nunca = pmfs[faixa]
        mask = eixo > obs
        sobrev = pmf[mask].sum() + nunca
        if sobrev <= 0:
            continue
        cond = np.where(mask, pmf, 0) / sobrev
        for venc, valor in g.groupby("data_vencimento").valor.sum().items():
            datas = venc + pd.to_timedelta(eixo[mask], unit="D")
            ok = (datas > corte) & (datas <= fim)
            esperado.loc[datas[ok]] += valor * cond[mask][ok]

    # realizado (só existe quando a data_corte do backtest está no passado)
    real = t[(t.titulo_id.isin(aberto.titulo_id))]
    pag_real = _q(con, "SELECT titulo_id, data_pagamento, valor_pago FROM fato_titulo WHERE data_pagamento IS NOT NULL")
    pag_real["data_pagamento"] = pd.to_datetime(pag_real.data_pagamento)
    pag_real = pag_real[pag_real.titulo_id.isin(real.titulo_id)
                        & (pag_real.data_pagamento > corte) & (pag_real.data_pagamento <= fim)]
    realizado = pag_real.groupby("data_pagamento").valor_pago.sum().reindex(esperado.index, fill_value=0)

    semana = ((esperado.index - (corte + pd.Timedelta(days=1))).days // 7) + 1
    out = pd.DataFrame({"semana": semana, "previsto": esperado.values, "realizado": realizado.values,
                        "data": esperado.index})
    out = out.groupby("semana").agg(inicio=("data", "min"), previsto=("previsto", "sum"),
                                    realizado=("realizado", "sum")).reset_index()
    out.attrs["data_corte"] = corte
    out.attrs["carteira_aberta"] = float(aberto.valor.sum())
    return out


def backtest_caixa(con, f: Filtros = TODOS, semanas: int = 13) -> pd.DataFrame:
    """Roda a previsão como se estivéssemos `semanas` atrás e compara com o realizado."""
    ultimo = pd.Timestamp(con.execute("SELECT MAX(data_ref) FROM dim_calendario").fetchone()[0])
    corte_bt = ultimo - pd.Timedelta(weeks=semanas)
    return previsao_caixa(con, f, data_corte=str(corte_bt.date()), semanas=semanas)
