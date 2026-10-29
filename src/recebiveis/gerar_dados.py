"""Gera uma base sintética de contas a receber de uma distribuidora B2B.

Simula o que sairia de um ERP: cadastro de clientes, títulos (duplicatas/boletos)
e baixas de pagamento. O comportamento de pagamento depende do score do cliente,
do segmento e de um choque macroeconômico em parte do período, para que as
métricas de crédito (roll rate, safras, perda) tenham padrões realistas.

Uso:
    python -m recebiveis.gerar_dados [--clientes 1500] [--meses 24]
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from recebiveis.config import BRONZE, DATA_CORTE, SEED

SEGMENTOS = {  # nome: (peso na base, efeito no risco)
    "Varejo": (0.30, 0.10),
    "Construção": (0.18, 0.35),
    "Alimentação": (0.22, -0.10),
    "Serviços": (0.15, 0.05),
    "Indústria": (0.15, -0.25),
}
PORTES = {  # nome: (peso, compras/mês, ticket médio R$)
    "MEI": (0.35, 1.2, 1_800),
    "ME": (0.35, 2.0, 4_500),
    "EPP": (0.22, 3.0, 11_000),
    "Médio": (0.08, 4.5, 32_000),
}
UFS = ["SP", "RJ", "MG", "PR", "SC", "RS", "BA", "GO", "PE", "ES"]
PESOS_UF = [0.34, 0.12, 0.13, 0.08, 0.06, 0.07, 0.06, 0.05, 0.05, 0.04]


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def gerar(n_clientes: int = 1500, meses: int = 24, data_corte: str = DATA_CORTE,
          seed: int = SEED) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    corte = pd.Timestamp(data_corte)
    inicio = (corte - pd.DateOffset(months=meses)).normalize() + pd.Timedelta(days=1)
    # choque macro: piora de crédito entre o 12º e o 17º mês do período
    choque_ini = inicio + pd.DateOffset(months=12)
    choque_fim = inicio + pd.DateOffset(months=17)

    # ---------------- clientes ----------------
    seg = rng.choice(list(SEGMENTOS), n_clientes, p=[v[0] for v in SEGMENTOS.values()])
    porte = rng.choice(list(PORTES), n_clientes, p=[v[0] for v in PORTES.values()])
    score = np.clip(rng.normal(640, 115, n_clientes), 300, 980).round().astype(int)
    dias_ate_cadastro = rng.integers(0, int((corte - inicio).days * 0.75), n_clientes)
    cadastro = inicio + pd.to_timedelta(dias_ate_cadastro, unit="D")
    ticket = np.array([PORTES[p][2] for p in porte])
    limite = (ticket * rng.uniform(2.5, 6, n_clientes)).round(-2)

    efeito_seg = np.array([SEGMENTOS[s][1] for s in seg])
    risco = (640 - score) / 90 + efeito_seg + rng.normal(0, 0.35, n_clientes)  # risco latente
    # probabilidade do cliente "quebrar" em algum momento do horizonte
    p_quebra = _sigmoid(-2.1 + 1.1 * risco)
    quebra = rng.random(n_clientes) < p_quebra
    janela = (corte + pd.DateOffset(months=6) - cadastro).days.values
    # a quebra acontece entre 15% e 100% da janela do cliente (parte cai no futuro)
    data_quebra = (cadastro + pd.to_timedelta(rng.uniform(0.05, 1, n_clientes) * janela, unit="D")).normalize()
    # o choque macro empurra clientes de risco para a quebra dentro da janela do choque
    p_quebra_choque = _sigmoid(-3.2 + 1.2 * risco)
    quebra_choque = (~quebra) & (cadastro < choque_ini) & (rng.random(n_clientes) < p_quebra_choque)
    dias_choque = rng.uniform(0, (choque_fim - choque_ini).days, n_clientes)
    data_quebra = data_quebra.where(~quebra_choque, (choque_ini + pd.to_timedelta(dias_choque, unit="D")).normalize())
    quebra = quebra | quebra_choque
    data_quebra = pd.DatetimeIndex(np.where(quebra, data_quebra, pd.NaT))

    clientes = pd.DataFrame({
        "cliente_id": [f"C{i:05d}" for i in range(1, n_clientes + 1)],
        "razao_social": [f"Cliente {i:05d} Ltda" for i in range(1, n_clientes + 1)],
        "segmento": seg, "porte": porte,
        "uf": rng.choice(UFS, n_clientes, p=PESOS_UF),
        "score_bureau": score, "limite_credito": limite,
        "data_cadastro": cadastro.normalize(),
    })

    # ---------------- títulos ----------------
    linhas = []
    titulo_seq = 0
    for i in range(n_clientes):
        lam = PORTES[porte[i]][1]
        fim_compras = min(corte, data_quebra[i]) if quebra[i] else corte
        mes = pd.Timestamp(cadastro[i]).to_period("M")
        while mes.to_timestamp() <= fim_compras:
            for _ in range(rng.poisson(lam)):
                dia = rng.integers(1, mes.days_in_month + 1)
                emissao = pd.Timestamp(year=mes.year, month=mes.month, day=int(dia))
                if emissao < cadastro[i] or emissao > fim_compras:
                    continue
                valor_pedido = float(np.round(rng.lognormal(np.log(ticket[i]), 0.55), 2))
                n_parc = rng.choice([1, 2, 3], p=[0.5, 0.3, 0.2])
                for k in range(1, n_parc + 1):
                    titulo_seq += 1
                    linhas.append((f"T{titulo_seq:07d}", clientes.cliente_id[i], emissao,
                                   emissao + pd.Timedelta(days=30 * k), k, n_parc,
                                   round(valor_pedido / n_parc, 2)))
            mes += 1
    titulos = pd.DataFrame(linhas, columns=["titulo_id", "cliente_id", "data_emissao",
                                            "data_vencimento", "parcela", "total_parcelas", "valor"])

    # ---------------- pagamentos ----------------
    idx = titulos.cliente_id.str[1:].astype(int).values - 1
    venc = titulos.data_vencimento
    r = risco[idx]
    em_choque = ((venc >= choque_ini) & (venc <= choque_fim)).values
    r_eff = r + np.where(em_choque, 0.6, 0.0)

    # títulos que vencem depois da quebra (com 30 dias de antecedência) não são pagos
    dq = pd.Series(data_quebra[idx])
    inadimplente = (quebra[idx] & (venc >= dq - pd.Timedelta(days=30))).values
    # parte dos inadimplentes é recuperada via cobrança/acordo depois de muito tempo
    recuperado = inadimplente & (rng.random(len(titulos)) < 0.22)

    p_atraso = _sigmoid(-0.9 + 0.9 * r_eff)
    atrasa = rng.random(len(titulos)) < p_atraso
    media_atraso = np.clip(6 + 9 * np.maximum(r_eff, -0.5), 2, 45)
    dias = np.where(atrasa, np.minimum(rng.exponential(media_atraso), 85),
                    -rng.integers(0, 4, len(titulos)))  # quem não atrasa paga até 3 dias antes
    dias = np.where(recuperado, rng.uniform(100, 360, len(titulos)), dias)
    pago = ~inadimplente | recuperado

    data_pag = (venc + pd.to_timedelta(np.round(dias), unit="D")).where(pago)
    pagamentos = pd.DataFrame({"titulo_id": titulos.titulo_id, "data_pagamento": data_pag,
                               "valor_pago": titulos.valor})
    pagamentos = pagamentos[pagamentos.data_pagamento.notna() & (pagamentos.data_pagamento <= corte)]
    pagamentos.insert(0, "pagamento_id", [f"P{i:07d}" for i in range(1, len(pagamentos) + 1)])

    # o ERP não conhece pagamentos/títulos futuros
    titulos = titulos[titulos.data_emissao <= corte]
    return {"clientes": clientes, "titulos": titulos, "pagamentos": pagamentos}


def salvar(tabelas: dict[str, pd.DataFrame]) -> None:
    BRONZE.mkdir(parents=True, exist_ok=True)
    for nome, df in tabelas.items():
        df.to_csv(BRONZE / f"{nome}.csv", index=False, date_format="%Y-%m-%d")
        print(f"  {nome:<11} {len(df):>8,} linhas -> {BRONZE / (nome + '.csv')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clientes", type=int, default=1500)
    ap.add_argument("--meses", type=int, default=24)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args()
    print("Gerando base sintética...")
    salvar(gerar(a.clientes, a.meses, seed=a.seed))


if __name__ == "__main__":
    main()
