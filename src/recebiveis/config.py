"""Configurações centrais do projeto."""
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DADOS = RAIZ / "data"
BRONZE = DADOS / "bronze"   # CSVs brutos, como viriam do ERP
GOLD = DADOS / "gold"       # marts em Parquet (prontos para Power BI / Looker)
BANCO = DADOS / "recebiveis.duckdb"
SQL = RAIZ / "sql"

DATA_CORTE = "2026-09-30"   # "hoje" na simulação
SEED = 42

# Faixas de atraso usadas em todo o projeto (ordem importa)
FAIXAS = ["A vencer", "1-30", "31-60", "61-90", "91-180", "180+"]

