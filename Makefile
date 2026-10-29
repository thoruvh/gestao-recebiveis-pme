.PHONY: setup dados etl app test all clean

setup:          ## instala o projeto e as dependências de desenvolvimento
	pip install -e ".[dev]"

dados:          ## gera a base sintética (bronze)
	python -m recebiveis.gerar_dados

etl:            ## bronze -> silver -> gold (DuckDB + Parquet)
	python -m recebiveis.etl

app:            ## abre o painel em http://localhost:8501
	streamlit run app/dashboard.py

test:
	pytest -q

all: dados etl app

clean:
	rm -rf data/
