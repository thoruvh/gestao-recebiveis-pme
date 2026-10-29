"""Pipeline bronze -> silver -> gold em DuckDB.

Lê os CSVs brutos (bronze), aplica os SQLs da pasta `sql/` e exporta os marts
gold em Parquet, que podem ser abertos direto no Power BI.

Uso:
    python -m recebiveis.etl
"""
from __future__ import annotations

import time

import duckdb

from recebiveis.config import BANCO, BRONZE, DATA_CORTE, GOLD, SQL


def conectar(somente_leitura: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(BANCO), read_only=somente_leitura)


def rodar(data_corte: str = DATA_CORTE) -> None:
    ini = time.perf_counter()
    BANCO.parent.mkdir(parents=True, exist_ok=True)
    con = conectar()
    con.execute(f"SET VARIABLE data_corte = '{data_corte}'")

    print("[bronze] carregando CSVs")
    for nome in ("clientes", "titulos", "pagamentos"):
        arq = BRONZE / f"{nome}.csv"
        if not arq.exists():
            raise FileNotFoundError(f"{arq} não encontrado. Rode antes: python -m recebiveis.gerar_dados")
        con.execute(f"CREATE OR REPLACE TABLE bronze_{nome} AS SELECT * FROM read_csv_auto('{arq.as_posix()}')")

    for arq_sql in sorted(SQL.glob("*.sql")):
        print(f"[{arq_sql.stem.split('_', 1)[1]}] {arq_sql.name}")
        con.execute(arq_sql.read_text(encoding="utf-8"))

    _checar_qualidade(con)

    GOLD.mkdir(parents=True, exist_ok=True)
    tabelas = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'gold_%' OR table_name LIKE 'dim_%' OR table_name = 'fato_titulo'").fetchall()]
    for t in sorted(tabelas):
        con.execute(f"COPY {t} TO '{(GOLD / t).as_posix()}.parquet' (FORMAT parquet)")
    print(f"[export] {len(tabelas)} tabelas em {GOLD}")
    con.close()
    print(f"Pipeline concluído em {time.perf_counter() - ini:.1f}s -> {BANCO}")


def _checar_qualidade(con: duckdb.DuckDBPyConnection) -> None:
    """Regras simples de qualidade: falham o pipeline se algo estiver errado."""
    regras = {
        "títulos sem cliente": "SELECT COUNT(*) FROM fato_titulo t LEFT JOIN dim_cliente c USING (cliente_id) WHERE c.cliente_id IS NULL",
        "títulos duplicados": "SELECT COUNT(*) - COUNT(DISTINCT titulo_id) FROM fato_titulo",
        "vencimento antes da emissão": "SELECT COUNT(*) FROM fato_titulo WHERE data_vencimento < data_emissao",
        "pagamento antes da emissão": "SELECT COUNT(*) FROM fato_titulo WHERE data_pagamento < data_emissao",
    }
    for nome, q in regras.items():
        n = con.execute(q).fetchone()[0]
        if n:
            raise ValueError(f"Falha de qualidade: {n} {nome}")
    print(f"[qualidade] {len(regras)} regras OK")


if __name__ == "__main__":
    rodar()
