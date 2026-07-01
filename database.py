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
            payload.get("data_transacao"),
            payload.get("data_vencimento"),
            payload.get("banco"),
        )


# ─── Expansão de parcelas ───────────────────────────────────────────────────────

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
    """
    Recebe uma transação e retorna uma lista de ocorrências mensais.
    - Único / Recorrente: 1 item usando data_vencimento (ou data_transacao).
    - Parcelado: N itens, um por mês a partir de data_vencimento.
    Retorna [] se não houver data válida.
    """
    tipo_pag = row.get("tipo_pagamento") or "unico"
    parcelas_total = row.get("parcelas_total") or 1
    valor_total = float(row.get("valor") or 0.0)

    data_venc = _to_date(row.get("data_vencimento")) or _to_date(row.get("data_transacao"))

    if data_venc is None:
        return []

    if tipo_pag != "parcelado" or parcelas_total <= 1:
        item = dict(row)
        item["vencimento_parcela"] = data_venc
        item["numero_parcela"] = 1
        item["parcelas_total"] = parcelas_total
        item["valor_parcela"] = valor_total
        return [item]

    valor_parcela = round(valor_total / parcelas_total, 2)
    resultado = []
    for i in range(parcelas_total):
        item = dict(row)
        item["vencimento_parcela"] = data_venc + relativedelta(months=i)
        item["numero_parcela"] = i + 1
        item["parcelas_total"] = parcelas_total
        item["valor_parcela"] = valor_parcela
        resultado.append(item)
    return resultado


def _filter_by_month(expanded: list[dict], ano: int, mes: int) -> list[dict]:
    resultado = []
    for item in expanded:
        venc = item.get("vencimento_parcela")
        if venc is None:
            continue
        venc = _to_date(venc)
        if venc is None:
            continue
        if venc.year == ano and venc.month == mes:
            resultado.append(item)
    return resultado


def _date_in_month(d, ano: int, mes: int) -> bool:
    d = _to_date(d)
    if d is None:
        return False
    return d.year == ano and d.month == mes


# ─── Fetch base ────────────────────────────────────────────────────────────────

async def _fetch_all_transacoes(telegram_user_id: str):
    async with pool.acquire() as conn:
        receitas = await conn.fetch("""
            SELECT * FROM transacoes
            WHERE telegram_user_id = $1 AND tipo = 'receita'
        """, str(telegram_user_id))

        desp_pessoal = await conn.fetch("""
            SELECT * FROM transacoes
            WHERE telegram_user_id = $1 AND tipo = 'despesa' AND escopo = 'pessoal'
        """, str(telegram_user_id))

        desp_ambos = await conn.fetch("""
            SELECT * FROM transacoes
            WHERE tipo = 'despesa' AND escopo = 'ambos'
        """)

    return (
        [dict(r) for r in receitas],
        [dict(r) for r in desp_pessoal],
        [dict(r) for r in desp_ambos],
    )


# ─── Relatórios ────────────────────────────────────────────────────────────────

async def get_monthly_summary(telegram_user_id: str, ano: int, mes: int) -> dict:
    receitas_raw, desp_pessoal_raw, desp_ambos_raw = await _fetch_all_transacoes(telegram_user_id)

    receitas = [
        r for r in receitas_raw
        if _date_in_month(r.get("data_transacao"), ano, mes)
    ]

    desp_pessoal = _filter_by_month(
        [item for r in desp_pessoal_raw for item in _expand_transacao(r)],
        ano, mes
    )
    desp_ambos = _filter_by_month(
        [item for r in desp_ambos_raw for item in _expand_transacao(r)],
        ano, mes
    )

    total_receitas = sum(float(r["valor"]) for r in receitas)
    total_pessoal  = sum(item["valor_parcela"] for item in desp_pessoal)
    total_ambos    = sum(item["valor_parcela"] for item in desp_ambos)
    total_lancado  = total_pessoal + total_ambos
    meu_custo_real = total_pessoal + (total_ambos * 0.5)
    sobra          = total_receitas - meu_custo_real

    def agrupar(rows, campo_valor="valor_parcela"):
        grupos = {}
        for r in rows:
            cat = r.get("categoria_text") or "Outros"
            grupos[cat] = grupos.get(cat, 0.0) + float(r.get(campo_valor, 0))
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
        "grupos_receitas": agrupar(receitas, campo_valor="valor"),
    }


async def get_months_with_installments() -> list[tuple[int, int]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT data_vencimento, parcelas_total
            FROM transacoes
            WHERE tipo_pagamento = 'parcelado'
              AND data_vencimento IS NOT NULL
              AND parcelas_total IS NOT NULL
        """)

    meses = set()
    hoje = date.today()

    for row in rows:
        data_venc = _to_date(row["data_vencimento"])
        if data_venc is None:
            continue
        parcelas = row["parcelas_total"] or 1
        for i in range(parcelas):
            venc = data_venc + relativedelta(months=i)
            if venc >= hoje.replace(day=1):
                meses.add((venc.year, venc.month))

    return sorted(meses)


async def get_installments_for_month(ano: int, mes: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM transacoes
            WHERE tipo_pagamento = 'parcelado'
              AND data_vencimento IS NOT NULL
        """)

    resultado = []
    for row in rows:
        expanded = _expand_transacao(dict(row))
        for item in expanded:
            v = item.get("vencimento_parcela")
            if v and v.year == ano and v.month == mes:
                resultado.append(item)

    return resultado

async def get_previous_balance(user_id: str, year: int, month: int) -> float:
    """Calcula a soma de todas as receitas menos despesas (com regra 50/50) de meses anteriores."""
    # Nota: usamos EXTRACT porque o banco armazena objetos DATE
    query = """
        SELECT 
            COALESCE(SUM(CASE WHEN tipo = 'receita' THEN valor ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN tipo = 'despesa' THEN 
                CASE WHEN escopo = 'ambos' THEN valor * 0.5 ELSE valor END
            ELSE 0 END), 0) as saldo_anterior
        FROM transacoes
        WHERE telegram_user_id = $1
          AND (
            EXTRACT(YEAR FROM data_transacao) < $2 
            OR (EXTRACT(YEAR FROM data_transacao) = $2 AND EXTRACT(MONTH FROM data_transacao) < $3)
          )
    """
    if pool is None:
        await init_db_pool()
        
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, str(user_id), int(year), int(month))
        return float(row['saldo_anterior'] or 0.0)