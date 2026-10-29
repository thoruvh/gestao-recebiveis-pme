-- =====================================================================
-- CAMADA SILVER: tipagem, limpeza e regras de qualidade sobre o bronze
-- =====================================================================

CREATE OR REPLACE TABLE dim_cliente AS
SELECT
    cliente_id,
    razao_social,
    segmento,
    porte,
    uf,
    CAST(score_bureau AS INTEGER)                 AS score_bureau,
    CASE
        WHEN score_bureau >= 750 THEN 'A (750+)'
        WHEN score_bureau >= 650 THEN 'B (650-749)'
        WHEN score_bureau >= 550 THEN 'C (550-649)'
        ELSE 'D (<550)'
    END                                           AS faixa_score,
    CAST(limite_credito AS DECIMAL(14, 2))        AS limite_credito,
    CAST(data_cadastro AS DATE)                   AS data_cadastro
FROM bronze_clientes;

-- um pagamento por título no ERP simulado; agregamos por segurança (baixas parciais)
CREATE OR REPLACE TABLE fato_titulo AS
WITH pag AS (
    SELECT titulo_id,
           MAX(CAST(data_pagamento AS DATE)) AS data_pagamento,
           SUM(valor_pago)                   AS valor_pago
    FROM bronze_pagamentos
    GROUP BY titulo_id
)
SELECT
    t.titulo_id,
    t.cliente_id,
    CAST(t.data_emissao AS DATE)                       AS data_emissao,
    CAST(t.data_vencimento AS DATE)                    AS data_vencimento,
    CAST(date_trunc('month', CAST(t.data_emissao AS DATE)) AS DATE) AS safra,
    t.parcela,
    t.total_parcelas,
    CAST(t.valor AS DECIMAL(14, 2))                    AS valor,
    p.data_pagamento,
    COALESCE(p.valor_pago, 0)                          AS valor_pago,
    datediff('day', CAST(t.data_vencimento AS DATE), p.data_pagamento) AS dias_atraso_pagamento
FROM bronze_titulos t
LEFT JOIN pag p USING (titulo_id)
WHERE t.valor > 0;

-- calendário de fechamentos mensais (último dia de cada mês até a data de corte)
CREATE OR REPLACE TABLE dim_calendario AS
SELECT CAST(last_day(CAST(d AS DATE)) AS DATE) AS data_ref
FROM generate_series(
        (SELECT CAST(date_trunc('month', MIN(data_emissao)) AS TIMESTAMP) FROM fato_titulo),
        CAST(getvariable('data_corte') AS TIMESTAMP),
        INTERVAL 1 MONTH) AS g(d)
WHERE last_day(CAST(d AS DATE)) <= CAST(getvariable('data_corte') AS DATE);

-- foto da carteira em aberto em cada fechamento: base de aging, roll rate e safras
-- títulos com mais de 360 dias de atraso são baixados como perda e saem da carteira
CREATE OR REPLACE TABLE fato_snapshot AS
SELECT
    c.data_ref,
    t.titulo_id,
    t.cliente_id,
    t.safra,
    t.valor,
    t.data_vencimento,
    GREATEST(datediff('day', t.data_vencimento, c.data_ref), 0) AS dias_atraso,
    CASE
        WHEN c.data_ref <= t.data_vencimento                         THEN 'A vencer'
        WHEN datediff('day', t.data_vencimento, c.data_ref) <= 30    THEN '1-30'
        WHEN datediff('day', t.data_vencimento, c.data_ref) <= 60    THEN '31-60'
        WHEN datediff('day', t.data_vencimento, c.data_ref) <= 90    THEN '61-90'
        WHEN datediff('day', t.data_vencimento, c.data_ref) <= 180   THEN '91-180'
        ELSE '180+'
    END AS faixa
FROM dim_calendario c
JOIN fato_titulo t
  ON t.data_emissao <= c.data_ref
 AND (t.data_pagamento IS NULL OR t.data_pagamento > c.data_ref)
 AND datediff('day', t.data_vencimento, c.data_ref) <= 360;
