import asyncpg
import logging
from config import DATABASE_URL
from datetime import date

logger = logging.getLogger(__name__)
pool: asyncpg.pool.Pool | None = None


async def init_db_pool():
    global pool
    if pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL não configurada no .env")
        pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5)
        logger.info("Pool de banco inicializado com sucesso.")


async def close_db_pool():
    global pool
    if pool:
        await pool.close()
        pool = None


async def is_user_authorized(telegram_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT authorized FROM usuarios WHERE telegram_id = $1",
            str(telegram_id)
        )
        return bool(row and row.get("authorized"))


async def authorize_user(telegram_id: str, nome: str | None = None, username: str | None = None):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO usuarios (telegram_id, nome, username, authorized)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (telegram_id) DO UPDATE
              SET nome = EXCLUDED.nome,
                  username = EXCLUDED.username,
                  authorized = TRUE
        """, str(telegram_id), nome, username)


async def insert_transacao(payload: dict):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transacoes
              (telegram_user_id, tipo, categoria_text, subcategoria_text,
               escopo, descricao, valor, forma_pagamento, tipo_pagamento,
               parcelas_total, data_transacao, data_vencimento, banco)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        """,
            str(payload.get("telegram_user_id")),
            payload.get("tipo"),
            payload.get("categoria_text"),
            payload.get("subcategoria_text"),
            payload.get("escopo"),
            payload.get("descricao"),
            float(payload.get("valor") or 0.0),
            payload.get("forma_pagamento"),
            payload.get("tipo_pagamento"),
            payload.get("parcelas_total"),
            payload.get("data_transacao"),   # já é datetime.date ou None
            payload.get("data_vencimento"),  # já é datetime.date ou None
            payload.get("banco"),
        )


# ─── Relatórios ────────────────────────────────────────────────────────────────

async def get_monthly_summary(telegram_user_id: str, ano: int, mes: int) -> dict:
    """
    Retorna o resumo financeiro de um mês específico para o usuário.
    Regra de visibilidade:
      - Receitas: apenas do próprio usuário
      - Despesas pessoais: apenas do próprio usuário
      - Despesas 'ambos': todas (50% entra no custo real do usuário)
    """
    async with pool.acquire() as conn:

        # Receitas do usuário no mês
        receitas_rows = await conn.fetch("""
            SELECT categoria_text, subcategoria_text, descricao, valor
            FROM transacoes
            WHERE telegram_user_id = $1
              AND tipo = 'receita'
              AND EXTRACT(YEAR  FROM data_transacao) = $2
              AND EXTRACT(MONTH FROM data_transacao) = $3
            ORDER BY data_transacao
        """, str(telegram_user_id), ano, mes)

        # Despesas pessoais do usuário no mês
        desp_pessoal_rows = await conn.fetch("""
            SELECT categoria_text, subcategoria_text, descricao, valor, escopo,
                   forma_pagamento, tipo_pagamento, parcelas_total, data_transacao, data_vencimento
            FROM transacoes
            WHERE telegram_user_id = $1
              AND tipo = 'despesa'
              AND escopo = 'pessoal'
              AND EXTRACT(YEAR  FROM data_transacao) = $2
              AND EXTRACT(MONTH FROM data_transacao) = $3
            ORDER BY data_transacao
        """, str(telegram_user_id), ano, mes)

        # Despesas compartilhadas (ambos) no mês — de qualquer usuário
        desp_ambos_rows = await conn.fetch("""
            SELECT categoria_text, subcategoria_text, descricao, valor, escopo,
                   forma_pagamento, tipo_pagamento, parcelas_total, data_transacao, data_vencimento,
                   telegram_user_id
            FROM transacoes
            WHERE tipo = 'despesa'
              AND escopo = 'ambos'
              AND EXTRACT(YEAR  FROM data_transacao) = $1
              AND EXTRACT(MONTH FROM data_transacao) = $2
            ORDER BY data_transacao
        """, ano, mes)

    receitas       = [dict(r) for r in receitas_rows]
    desp_pessoal   = [dict(r) for r in desp_pessoal_rows]
    desp_ambos     = [dict(r) for r in desp_ambos_rows]

    total_receitas      = sum(float(r["valor"]) for r in receitas)
    total_pessoal       = sum(float(r["valor"]) for r in desp_pessoal)
    total_ambos         = sum(float(r["valor"]) for r in desp_ambos)
    total_lancado       = total_pessoal + total_ambos
    meu_custo_real      = total_pessoal + (total_ambos * 0.5)
    sobra               = total_receitas - meu_custo_real

    # Agrupar despesas por categoria
    def agrupar(rows):
        grupos = {}
        for r in rows:
            cat = r["categoria_text"] or "Outros"
            grupos.setdefault(cat, 0.0)
            grupos[cat] += float(r["valor"])
        return grupos

    return {
        "ano": ano,
        "mes": mes,
        "receitas": receitas,
        "total_receitas": round(total_receitas, 2),
        "desp_pessoal": desp_pessoal,
        "desp_ambos": desp_ambos,
        "total_pessoal": round(total_pessoal, 2),
        "total_ambos": round(total_ambos, 2),
        "total_lancado": round(total_lancado, 2),
        "meu_custo_real": round(meu_custo_real, 2),
        "sobra": round(sobra, 2),
        "grupos_pessoal": agrupar(desp_pessoal),
        "grupos_ambos": agrupar(desp_ambos),
        "grupos_receitas": agrupar(receitas),
    }


async def get_months_with_installments() -> list[tuple[int, int]]:
    """
    Retorna lista de (ano, mes) de todos os meses futuros que possuem
    parcelas previstas (data_vencimento >= hoje).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT
                EXTRACT(YEAR  FROM data_vencimento)::int AS ano,
                EXTRACT(MONTH FROM data_vencimento)::int AS mes
            FROM transacoes
            WHERE tipo_pagamento = 'parcelado'
              AND data_vencimento >= CURRENT_DATE
            ORDER BY ano, mes
        """)
    return [(r["ano"], r["mes"]) for r in rows]


async def get_installments_for_month(ano: int, mes: int) -> list[dict]:
    """
    Retorna as parcelas previstas para um mês futuro.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT descricao, categoria_text, valor, parcelas_total,
                   data_transacao, data_vencimento, escopo, telegram_user_id
            FROM transacoes
            WHERE tipo_pagamento = 'parcelado'
              AND EXTRACT(YEAR  FROM data_vencimento) = $1
              AND EXTRACT(MONTH FROM data_vencimento) = $2
            ORDER BY data_vencimento
        """, ano, mes)
    return [dict(r) for r in rows]