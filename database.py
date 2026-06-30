import asyncpg
import logging

logger = logging.getLogger(__name__)
pool = None

async def init_db_pool():
    import os
    global pool
    pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"), min_size=1, max_size=5)
    logger.info("Pool de banco inicializado com sucesso.")

async def close_db_pool():
    global pool
    if pool:
        await pool.close()

async def is_user_authorized(telegram_id: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM usuarios WHERE telegram_id = $1 AND authorized = TRUE",
            telegram_id
        )
        return row is not None

async def authorize_user(telegram_id: str, nome: str, username: str):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO usuarios (telegram_id, nome, username, authorized)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (telegram_id) DO UPDATE SET authorized = TRUE
        """, telegram_id, nome, username or "")

async def insert_transacao(payload: dict):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transacoes (
                telegram_user_id,
                tipo,
                categoria_text,
                subcategoria_text,
                escopo,
                descricao,
                valor,
                forma_pagamento,
                tipo_pagamento
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """,
            payload["telegram_user_id"],
            payload["tipo"],
            payload["categoria_text"],
            payload["subcategoria_text"],
            payload["escopo"],
            payload["descricao"],
            payload["valor"],
            payload.get("forma_pagamento", ""),
            payload.get("tipo_pagamento", "")
        )

async def get_summary_for_user(telegram_user_id: str) -> dict:
    async with pool.acquire() as conn:
        receitas = await conn.fetchval("""
            SELECT COALESCE(SUM(valor), 0) FROM transacoes
            WHERE telegram_user_id = $1 AND tipo = 'receita'
        """, telegram_user_id)

        desp_pessoal = await conn.fetchval("""
            SELECT COALESCE(SUM(valor), 0) FROM transacoes
            WHERE telegram_user_id = $1 AND tipo = 'despesa' AND escopo = 'pessoal'
        """, telegram_user_id)

        desp_ambos = await conn.fetchval("""
            SELECT COALESCE(SUM(valor), 0) FROM transacoes
            WHERE tipo = 'despesa' AND escopo = 'ambos'
        """)

        minhas_reais = float(desp_pessoal) + float(desp_ambos) * 0.5
        saldo = float(receitas) - minhas_reais

        return {
            "receitas": float(receitas),
            "despesas_pessoais": float(desp_pessoal),
            "despesas_compartilhadas_total": float(desp_ambos),
            "minhas_despesas_reais": minhas_reais,
            "saldo": saldo
        }