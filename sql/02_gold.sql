-- =====================================================================
-- CAMADA GOLD: marts agregados, prontos para consumo em BI
-- =====================================================================

-- Aging mensal da carteira
CREATE OR REPLACE TABLE gold_aging AS
SELECT s.data_ref, s.faixa, c.segmento, c.porte, c.faixa_score,
       COUNT(*)      AS qtd_titulos,
       SUM(s.valor)  AS saldo
FROM fato_snapshot s
JOIN dim_cliente c USING (cliente_id)
GROUP BY ALL;

-- Roll rate: para onde foi o saldo de cada faixa no fechamento seguinte
CREATE OR REPLACE TABLE gold_roll_rate AS
SELECT a.data_ref,
       a.faixa                          AS faixa_origem,
       CASE
           WHEN b.faixa IS NOT NULL THEN b.faixa
           WHEN t.data_pagamento <= CAST(last_day(a.data_ref + INTERVAL 1 MONTH) AS DATE) THEN 'Liquidado'
           ELSE 'Baixa (perda)'
       END                              AS faixa_destino,
       c.segmento, c.porte, c.faixa_score,
       SUM(a.valor)                     AS saldo
FROM fato_snapshot a
JOIN dim_cliente c USING (cliente_id)
JOIN fato_titulo t ON t.titulo_id = a.titulo_id
LEFT JOIN fato_snapshot b
       ON b.titulo_id = a.titulo_id
      AND b.data_ref  = CAST(last_day(a.data_ref + INTERVAL 1 MONTH) AS DATE)
WHERE a.data_ref < (SELECT MAX(data_ref) FROM dim_calendario)
GROUP BY ALL;

-- Safras (vintage): % do valor emitido que já atingiu 90+ dias de atraso (ever 90+)
CREATE OR REPLACE TABLE gold_safras AS
WITH primeiro_90 AS (
    SELECT titulo_id, MIN(data_ref) AS data_90
    FROM fato_snapshot
    WHERE dias_atraso > 90
    GROUP BY titulo_id
),
grade AS (
    SELECT DISTINCT t.safra, cal.data_ref,
           datediff('month', t.safra, cal.data_ref) AS mob   -- months on book
    FROM fato_titulo t
    JOIN dim_calendario cal ON cal.data_ref >= t.safra
)
SELECT g.safra, g.mob, g.data_ref, c.segmento, c.porte, c.faixa_score,
       SUM(t.valor)                                                   AS valor_emitido,
       SUM(CASE WHEN p.data_90 <= g.data_ref THEN t.valor ELSE 0 END) AS valor_ever90
FROM grade g
JOIN fato_titulo t ON t.safra = g.safra
JOIN dim_cliente c USING (cliente_id)
LEFT JOIN primeiro_90 p USING (titulo_id)
GROUP BY ALL;

-- Faturamento e recebimento mensal (para DSO e fluxo realizado)
CREATE OR REPLACE TABLE gold_fluxo_mensal AS
SELECT CAST(date_trunc('month', data_emissao) AS DATE) AS mes, c.segmento, c.porte, c.faixa_score,
       SUM(valor) AS faturado, 0 AS recebido
FROM fato_titulo JOIN dim_cliente c USING (cliente_id) GROUP BY ALL
UNION ALL
SELECT CAST(date_trunc('month', data_pagamento) AS DATE), c.segmento, c.porte, c.faixa_score,
       0, SUM(valor_pago)
FROM fato_titulo JOIN dim_cliente c USING (cliente_id)
WHERE data_pagamento IS NOT NULL GROUP BY ALL;
