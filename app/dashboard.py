"""Painel de Recebíveis & Inadimplência — Streamlit.

    streamlit run app/dashboard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from recebiveis import metricas as m  # noqa: E402
from recebiveis.config import BANCO, FAIXAS  # noqa: E402
from recebiveis.etl import conectar  # noqa: E402

# ------------------------------------------------------------------ paleta
SUPERFICIE, TINTA, TINTA_2, MUTED, GRADE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
# rampa ordinal (azul) para faixas de atraso: mais escuro = mais atrasado
RAMPA_FAIXAS = dict(zip(FAIXAS, ["#b7d3f6", "#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"], strict=True))
FONTE = "system-ui, -apple-system, Segoe UI, sans-serif"

st.set_page_config(page_title="Recebíveis & Inadimplência", page_icon="📊", layout="wide")


def brl(v: float, casas: int = 1) -> str:
    if abs(v) >= 1e6:
        s = f"R$ {v / 1e6:,.{casas}f} mi"
    elif abs(v) >= 1e3:
        s = f"R$ {v / 1e3:,.0f} mil"
    else:
        s = f"R$ {v:,.0f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v: float, casas: int = 1) -> str:
    return f"{v * 100:.{casas}f}%".replace(".", ",")


def estilo(fig: go.Figure, altura: int = 360, legenda: bool = True) -> go.Figure:
    fig.update_layout(
        height=altura, margin=dict(l=8, r=8, t=36, b=8), plot_bgcolor=SUPERFICIE, paper_bgcolor=SUPERFICIE,
        font=dict(family=FONTE, color=TINTA_2, size=13), showlegend=legenda,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, font=dict(color=TINTA_2)),
        hoverlabel=dict(bgcolor="white", font=dict(family=FONTE, color=TINTA)), bargap=0.25,
        separators=",.",
    )
    fig.update_xaxes(showgrid=False, linecolor="#c3c2b7", tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRADE, zeroline=False, tickfont=dict(color=MUTED))
    return fig


@st.cache_resource
def con():
    return conectar(somente_leitura=True)


if not BANCO.exists():
    st.error("Banco não encontrado. Rode `make dados etl` (ou `python -m recebiveis.gerar_dados` "
             "e `python -m recebiveis.etl`) antes de abrir o painel.")
    st.stop()

c = con()
dims = c.execute("SELECT DISTINCT segmento, porte, faixa_score FROM dim_cliente").df()

# ------------------------------------------------------------------ cabeçalho + filtros
st.title("Recebíveis & Inadimplência")
st.caption("Carteira B2B de uma distribuidora fictícia · dados sintéticos · fechamento mensal")

f1, f2, f3 = st.columns(3)
seg = f1.multiselect("Segmento", sorted(dims.segmento.unique()), placeholder="Todos")
por = f2.multiselect("Porte", ["MEI", "ME", "EPP", "Médio"], placeholder="Todos")
sco = f3.multiselect("Faixa de score", sorted(dims.faixa_score.unique()), placeholder="Todas")
F = m.Filtros(seg or None, por or None, sco or None)

k = m.kpis(c, F)
st.caption(f"Posição em {k['data_ref']:%d/%m/%Y}")
pe = m.perda_esperada(c, F)
k1, k2, k3, k4, k5 = st.columns(5)
SEM_SETA = dict(delta_color="off", delta_arrow="off", border=True)
k1.metric("Carteira em aberto", brl(k["carteira"]), "títulos não liquidados", **SEM_SETA)
k2.metric("Vencido", brl(k["vencido"]), pct(k["pct_vencido"]) + " da carteira", **SEM_SETA)
k3.metric("Inadimplência 90+", pct(k["pct_over90"]), brl(k["over90"]), **SEM_SETA)
k4.metric("Perda esperada", brl(pe.perda_esperada.sum()),
          pct(pe.perda_esperada.sum() / k["carteira"]) + " da carteira", **SEM_SETA)
k5.metric("DSO", f"{k['dso']:.0f} dias", "prazo médio de recebimento", **SEM_SETA)

# os 6 primeiros fechamentos são formação da carteira (poucos títulos vencidos): ficam fora das taxas
INICIO_TAXAS = pd.Timestamp(c.execute("SELECT MIN(data_ref) FROM dim_calendario").fetchone()[0]) + pd.DateOffset(months=6)

abas = st.tabs(["Aging", "Roll rate", "Safras", "Perda esperada", "Fluxo de caixa", "Maiores exposições"])

# ------------------------------------------------------------------ Aging
with abas[0]:
    ag = m.aging(c, F)
    st.subheader("Saldo vencido por faixa de atraso")
    st.caption("Evolução do saldo em atraso a cada fechamento. Títulos com mais de 360 dias são baixados como perda.")
    fig = go.Figure()
    for faixa in FAIXAS[1:]:
        d = ag[ag.faixa == faixa]
        fig.add_bar(x=d.data_ref, y=d.saldo, name=faixa, marker_color=RAMPA_FAIXAS[faixa],
                    marker_line=dict(color=SUPERFICIE, width=1),
                    hovertemplate=f"<b>{faixa} dias</b><br>%{{x|%b/%Y}}<br>R$ %{{y:,.0f}}<extra></extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(tickformat="%m/%Y", dtick="M3")
    fig.update_yaxes(tickprefix="R$ ", tickformat=",.2s")
    st.plotly_chart(estilo(fig, 380), width="stretch")

    col1, col2 = st.columns([3, 2])
    ag_t = ag[ag.data_ref >= INICIO_TAXAS]
    tot = ag_t.groupby("data_ref").saldo.sum()
    o90 = ag_t[ag_t.faixa.isin(["91-180", "180+"])].groupby("data_ref").saldo.sum().reindex(tot.index, fill_value=0)
    venc = ag_t[ag_t.faixa != "A vencer"].groupby("data_ref").saldo.sum().reindex(tot.index, fill_value=0)
    with col1:
        st.subheader("% da carteira em atraso")
        fig = go.Figure()
        for nome, serie, cor in (("Vencido total", venc / tot, SERIES[0]), ("Atraso 90+", o90 / tot, SERIES[1])):
            fig.add_scatter(x=serie.index, y=serie.values, name=nome, mode="lines", line=dict(width=2, color=cor),
                            hovertemplate=f"<b>{nome}</b><br>%{{x|%b/%Y}}: %{{y:.1%}}<extra></extra>")
            fig.add_annotation(x=serie.index[-1], y=serie.values[-1], text=f"{nome} {pct(serie.values[-1])}",
                               showarrow=False, xanchor="right", yanchor="bottom", font=dict(color=TINTA_2))
        fig.update_yaxes(tickformat=".0%", rangemode="tozero")
        fig.update_xaxes(tickformat="%m/%Y", dtick="M3")
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(estilo(fig, 320), width="stretch")
    with col2:
        st.subheader("Posição atual")
        atual = ag[ag.data_ref == ag.data_ref.max()][["faixa", "qtd", "saldo"]].copy()
        atual["% carteira"] = atual.saldo / atual.saldo.sum()
        atual["saldo"] = atual.saldo.round(0)
        st.dataframe(atual.rename(columns={"faixa": "Faixa", "qtd": "Títulos", "saldo": "Saldo (R$)"}),
                     hide_index=True, width="stretch",
                     column_config={"Saldo (R$)": st.column_config.NumberColumn(format="localized"),
                                    "% carteira": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})

# ------------------------------------------------------------------ Roll rate
with abas[1]:
    meses = st.slider("Janela da média (meses)", 3, 12, 6, key="rr")
    mat = m.matriz_roll_rate(c, F, meses)
    st.subheader("Matriz de transição entre fechamentos")
    st.caption("Linha = faixa no mês t; coluna = para onde o saldo foi no mês t+1 (média ponderada por saldo).")
    z = mat.values
    fig = go.Figure(go.Heatmap(
        z=z, x=list(mat.columns), y=list(mat.index), colorscale=[[0, "#f4f8fd"], [0.5, "#5598e7"], [1, "#0d366b"]],
        zmin=0, zmax=1, showscale=False, xgap=2, ygap=2,
        text=[[pct(v, 0) if v >= 0.005 else "" for v in row] for row in z], texttemplate="%{text}",
        hovertemplate="De <b>%{y}</b> para <b>%{x}</b>: %{z:.1%}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", title="Faixa no mês t", gridcolor=SUPERFICIE)
    fig.update_xaxes(title="Destino no mês t+1", side="top")
    st.plotly_chart(estilo(fig, 400, legenda=False), width="stretch")

    st.subheader("Roll forward: % do saldo que piorou de faixa")
    ev = m.evolucao_roll_rate(c, F)
    ev = ev[ev.data_ref >= INICIO_TAXAS]
    fig = go.Figure()
    for i, (tr, d) in enumerate(ev.groupby("transicao", sort=False)):
        fig.add_scatter(x=d.data_ref, y=d.roll_forward, name=tr, mode="lines",
                        line=dict(width=2, color=SERIES[i % len(SERIES)]),
                        hovertemplate=f"<b>{tr}</b><br>%{{x|%b/%Y}}: %{{y:.1%}}<extra></extra>")
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    fig.update_xaxes(tickformat="%m/%Y", dtick="M3")
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(estilo(fig, 340), width="stretch")

# ------------------------------------------------------------------ Safras
with abas[2]:
    sf = m.safras(c, F)
    st.subheader("Curva de safras: % do valor emitido que já atingiu 90+ dias de atraso")
    safras_ord = sorted(sf.safra.unique())
    ini = pd.Timestamp(safras_ord[0])
    # safras cujos vencimentos caíram na janela do choque macro simulado
    choque = (ini + pd.DateOffset(months=11), ini + pd.DateOffset(months=16))
    st.caption(f"Cada linha é um mês de emissão. As safras de {choque[0]:%m/%Y} a {choque[1]:%m/%Y}, que venceram "
               "durante o choque econômico simulado, aparecem em laranja.")

    def grupo(s):
        s = pd.Timestamp(s)
        return "Safras do choque" if choque[0] <= s <= choque[1] else (
            "Antes do choque" if s < choque[0] else "Depois do choque")
    cores = {"Antes do choque": SERIES[0], "Safras do choque": SERIES[1], "Depois do choque": SERIES[2]}
    fig = go.Figure()
    vistos = set()
    for s, d in sf[sf.mob <= 12].groupby("safra"):
        g = grupo(s)
        fig.add_scatter(x=d.mob, y=d.pct_ever90, mode="lines", line=dict(width=2, color=cores[g]),
                        name=g, legendgroup=g, showlegend=g not in vistos, opacity=0.85,
                        hovertemplate=f"Safra <b>{pd.Timestamp(s):%m/%Y}</b><br>MOB %{{x}}: %{{y:.2%}}<extra></extra>")
        vistos.add(g)
    fig.update_xaxes(title="Meses desde a emissão (MOB)", dtick=1)
    fig.update_yaxes(tickformat=".1%", rangemode="tozero")
    st.plotly_chart(estilo(fig, 420), width="stretch")
    tab = sf[sf.mob.isin([3, 6, 9, 12])].pivot(index="safra", columns="mob", values="pct_ever90")
    tab.index = pd.to_datetime(tab.index).strftime("%Y-%m")
    tab.columns = [f"MOB {c}" for c in tab.columns]
    with st.expander("Ver tabela de safras"):
        st.dataframe((tab * 100).round(2).astype(str).replace("nan", "") + "%", width="stretch")

# ------------------------------------------------------------------ Perda esperada
with abas[3]:
    st.subheader("Perda esperada da carteira atual")
    st.caption("Probabilidade de cada faixa terminar em baixa, calculada como cadeia de Markov absorvente "
               "sobre a matriz de roll rate (estados finais: liquidado ou baixa). Perda = saldo × probabilidade.")
    meses_pe = st.slider("Janela da matriz (meses)", 3, 12, 6, key="pe")
    pe = m.perda_esperada(c, F, meses_pe)
    col1, col2 = st.columns([3, 2])
    with col1:
        fig = go.Figure(go.Bar(
            x=pe.faixa, y=pe.perda_esperada, marker_color=[RAMPA_FAIXAS[f] for f in pe.faixa],
            text=[brl(v) for v in pe.perda_esperada], textposition="outside", textfont=dict(color=TINTA_2),
            customdata=pe.prob_perda, hovertemplate="<b>%{x}</b><br>Perda esperada: R$ %{y:,.0f}"
                                                    "<br>Prob. de perda: %{customdata:.1%}<extra></extra>"))
        fig.update_yaxes(tickprefix="R$ ", tickformat=",.2s")
        fig.update_xaxes(title="Faixa de atraso")
        st.plotly_chart(estilo(fig, 380, legenda=False), width="stretch")
    with col2:
        t = pe.assign(saldo=pe.saldo.round(0), perda_esperada=pe.perda_esperada.round(0))
        t = t.rename(columns={"faixa": "Faixa", "saldo": "Saldo (R$)", "prob_perda": "Prob. perda",
                               "perda_esperada": "Perda esperada (R$)"})
        st.dataframe(t, hide_index=True, width="stretch", column_config={
            "Saldo (R$)": st.column_config.NumberColumn(format="localized"),
            "Perda esperada (R$)": st.column_config.NumberColumn(format="localized"),
            "Prob. perda": st.column_config.NumberColumn(format="percent")})
        st.metric("Total", brl(pe.perda_esperada.sum()),
                  pct(pe.perda_esperada.sum() / pe.saldo.sum()) + " da carteira", delta_color="off",
                  delta_arrow="off")

# ------------------------------------------------------------------ Fluxo de caixa
with abas[4]:
    fc = m.previsao_caixa(c, F)
    st.subheader("Previsão de recebimento da carteira atual — próximas 13 semanas")
    st.caption("Distribuição empírica de atraso por faixa de score, condicionada ao atraso que cada título já tem. "
               "Não inclui vendas futuras.")
    fig = go.Figure(go.Bar(x=fc.inicio, y=fc.previsto, marker_color=SERIES[0], name="Previsto",
                           hovertemplate="Semana de %{x|%d/%m}<br>R$ %{y:,.0f}<extra></extra>"))
    fig.update_yaxes(tickprefix="R$ ", tickformat=",.2s")
    fig.update_xaxes(tickformat="%d/%m")
    st.plotly_chart(estilo(fig, 340, legenda=False), width="stretch")
    a, b = st.columns(2)
    a.metric("Previsto em 13 semanas", brl(fc.previsto.sum()))
    b.metric("% da carteira em aberto", pct(fc.previsto.sum() / fc.attrs["carteira_aberta"]))

    bt = m.backtest_caixa(c, F)
    erro = bt.previsto.sum() / bt.realizado.sum() - 1 if bt.realizado.sum() else float("nan")
    st.subheader("Backtest: o modelo acertaria?")
    st.caption(f"Previsão feita com os dados disponíveis em {bt.attrs['data_corte']:%d/%m/%Y}, "
               f"comparada com o que de fato entrou no caixa. Erro no total das 13 semanas: {pct(erro)}.")
    fig = go.Figure()
    fig.add_bar(x=bt.inicio, y=bt.realizado, name="Realizado", marker_color="#b7d3f6",
                hovertemplate="Semana de %{x|%d/%m}<br>Realizado: R$ %{y:,.0f}<extra></extra>")
    fig.add_scatter(x=bt.inicio, y=bt.previsto, name="Previsto", mode="lines+markers",
                    line=dict(width=2, color=SERIES[1]), marker=dict(size=8, line=dict(color=SUPERFICIE, width=2)),
                    hovertemplate="Semana de %{x|%d/%m}<br>Previsto: R$ %{y:,.0f}<extra></extra>")
    fig.update_yaxes(tickprefix="R$ ", tickformat=",.2s")
    fig.update_xaxes(tickformat="%d/%m")
    st.plotly_chart(estilo(fig, 340), width="stretch")

# ------------------------------------------------------------------ Concentração
with abas[5]:
    st.subheader("Maiores exposições por cliente")
    top = m.concentracao(c, F, top=15)
    total = k["carteira"] or 1
    st.caption(f"Os 15 maiores clientes concentram {pct(top.saldo.sum() / total)} da carteira filtrada.")
    top["% carteira"] = top.saldo / total
    top[["saldo", "vencido"]] = top[["saldo", "vencido"]].round(0)
    st.dataframe(top.rename(columns={"cliente_id": "ID", "razao_social": "Cliente", "segmento": "Segmento",
                                     "porte": "Porte", "faixa_score": "Score", "saldo": "Saldo (R$)",
                                     "vencido": "Vencido (R$)", "maior_atraso": "Maior atraso (dias)"}),
                 hide_index=True, width="stretch", height=36 * (len(top) + 1) + 3, column_config={
                     "Saldo (R$)": st.column_config.NumberColumn(format="localized"),
                     "Vencido (R$)": st.column_config.NumberColumn(format="localized"),
                     "% carteira": st.column_config.ProgressColumn(format="percent", min_value=0,
                                                                   max_value=float(top["% carteira"].max()))})

st.divider()
st.caption("Projeto de portfólio · dados 100% sintéticos · código em github.com/<seu-usuario>/gestao-recebiveis-pme")
