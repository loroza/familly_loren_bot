# database.py
import asyncpg
import logging
from datetime import date
from dateutil.relativedelta import relativedelta
from config import DATABASE_URL

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
               parcelas_total, data_transacao, data_vencimento, banco,
               data_registro, criado_em, status, data_pagamento)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
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
            payload.get("data_transacao"),
            payload.get("data_vencimento"),
            payload.get("banco"),
            payload.get("data_registro"),
            payload.get("criado_em"),
            payload.get("status", "realizado"),
            payload.get("data_pagamento"),
        )


async def update_transacao_to_realizado(transacao_id: int, data_pagamento: str):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE transacoes
            SET status = 'realizado',
                data_pagamento = CAST($1 AS DATE)
            WHERE id = $2
        """, data_pagamento, transacao_id)


async def get_pendentes_by_month(user_id: str, ano: int, mes: int):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM transacoes
            WHERE telegram_user_id = $1
              AND status = 'previsto'
              AND (
                  (data_vencimento IS NOT NULL AND EXTRACT(YEAR FROM data_vencimento) = $2 AND EXTRACT(MONTH FROM data_vencimento) = $3)
                  OR
                  (data_vencimento IS NULL AND EXTRACT(YEAR FROM data_transacao) = $2 AND EXTRACT(MONTH FROM data_transacao) = $3)
              )
            ORDER BY data_vencimento, data_transacao
        """, str(user_id), ano, mes)
    return [dict(r) for r in rows]


# ─── Auxiliares de Data ────

def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _expand_transacao(row: dict) -> list[dict]:
    tipo_pag = row.get("tipo_pagamento") or "unica"
    parcelas_total = row.get("parcelas_total") or 1
    valor_total = float(row.get("valor") or 0.0)

    data_ref = _to_date(row.get("data_vencimento")) or _to_date(row.get("data_transacao"))

    if data_ref is None:
        return []

    if tipo_pag != "parcelado" or parcelas_total <= 1:
        item = dict(row)
        item["vencimento_parcela"] = data_ref
        item["numero_parcela"] = 1
        item["parcelas_total"] = 1
        item["valor_parcela"] = valor_total
        return [item]

    valor_parcela = round(valor_total / parcelas_total, 2)
    resultado = []
    for i in range(parcelas_total):
        item = dict(row)
        item["vencimento_parcela"] = data_ref + relativedelta(months=i)
        item["numero_parcela"] = i + 1
        item["parcelas_total"] = parcelas_total
        item["valor_parcela"] = valor_parcela
        resultado.append(item)
    return resultado


# ─── Fetch e Agrupamento ────

async def _fetch_all_transacoes(telegram_user_id: str):
    async with pool.acquire() as conn:
        receitas = await conn.fetch(
            "SELECT * FROM transacoes WHERE telegram_user_id = $1 AND tipo = 'receita'",
            str(telegram_user_id)
        )
        desp_pessoal = await conn.fetch(
            "SELECT * FROM transacoes WHERE telegram_user_id = $1 AND tipo = 'despesa' AND escopo = 'pessoal'",
            str(telegram_user_id)
        )
        desp_ambos = await conn.fetch(
            "SELECT * FROM transacoes WHERE tipo = 'despesa' AND escopo = 'ambos'"
        )

    return [dict(r) for r in receitas], [dict(r) for r in desp_pessoal], [dict(r) for r in desp_ambos]


async def get_monthly_summary(telegram_user_id: str, ano: int, mes: int) -> dict:
    rec_raw, dp_raw, da_raw = await _fetch_all_transacoes(telegram_user_id)

    receitas = [r for r in rec_raw if (d := _to_date(r.get("data_transacao"))) and d.year == ano and d.month == mes]

    desp_pessoal = []
    for r in dp_raw:
        desp_pessoal.extend([
            item for item in _expand_transacao(r)
            if (v := item["vencimento_parcela"]) and v.year == ano and v.month == mes
        ])

    desp_ambos = []
    for r in da_raw:
        desp_ambos.extend([
            item for item in _expand_transacao(r)
            if (v := item["vencimento_parcela"]) and v.year == ano and v.month == mes
        ])

    total_receitas = sum(float(r["valor"]) for r in receitas)
    total_pessoal = sum(i["valor_parcela"] for i in desp_pessoal)
    total_ambos = sum(i["valor_parcela"] for i in desp_ambos)

    meu_custo_real = total_pessoal + (total_ambos * 0.5)
    sobra = total_receitas - meu_custo_real

    def agrupar(rows, campo_valor="valor_parcela"):
        grupos = {}
        for r in rows:
            cat = (r.get("categoria_text") or "Outros").strip()
            grupos[cat] = grupos.get(cat, 0.0) + float(r.get(campo_valor, 0))
        return grupos

    return {
        "ano": ano, "mes": mes,
        "receitas": receitas, "total_receitas": round(total_receitas, 2),
        "desp_pessoal": desp_pessoal, "desp_ambos": desp_ambos,
        "total_pessoal": round(total_pessoal, 2),
        "total_ambos": round(total_ambos, 2),
        "total_lancado": round(total_pessoal + total_ambos, 2),
        "meu_custo_real": round(meu_custo_real, 2),
        "sobra": round(sobra, 2),
        "grupos_pessoal": agrupar(desp_pessoal),
        "grupos_ambos": agrupar(desp_ambos),
        "grupos_receitas": agrupar(receitas, "valor")
    }


async def get_previous_balance(user_id: str, year: int, month: int) -> float:
    rec_raw, dp_raw, da_raw = await _fetch_all_transacoes(user_id)

    todas_transacoes = rec_raw + dp_raw + da_raw
    if not todas_transacoes:
        return 0.0

    datas = []
    for t in todas_transacoes:
        d = _to_date(t.get("data_vencimento")) or _to_date(t.get("data_transacao"))
        if d:
            datas.append(d)

    if not datas:
        return 0.0

    start_date = min(datas).replace(day=1)
    target_date = date(year, month, 1)

    saldo_acumulado = 0.0
    current = start_date
    while current < target_date:
        resumo = await get_monthly_summary(user_id, current.year, current.month)
        saldo_acumulado += resumo["sobra"]
        current += relativedelta(months=1)

    return round(saldo_acumulado, 2)


async def get_months_with_installments():
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT data_vencimento, parcelas_total FROM transacoes WHERE tipo_pagamento = 'parcelado'"
        )

    meses = set()
    hoje = date.today().replace(day=1)
    for r in rows:
        d = _to_date(r["data_vencimento"])
        if not d:
            continue
        for i in range(r["parcelas_total"] or 1):
            venc = d + relativedelta(months=i)
            if venc >= hoje:
                meses.add((venc.year, venc.month))
    return sorted(meses)