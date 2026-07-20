# database.py
import asyncpg
import logging
from datetime import date, datetime
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

async def get_all_authorized_users() -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id FROM usuarios WHERE authorized = TRUE")
        return [row['telegram_id'] for row in rows]

async def insert_transacao(payload: dict):
    """Insere transações no banco, tratando parcelamento real (Opção B)."""
    async with pool.acquire() as conn:
        tipo_pag = payload.get("tipo_pagamento")
        total_parcelas = payload.get("parcelas_total") or 1
        
        if tipo_pag != "parcelado":
            total_parcelas = 1

        valor_total = float(payload.get("valor") or 0.0)
        valor_parcela = round(valor_total / total_parcelas, 2)
        
        data_base_venc = _to_date(payload.get("data_vencimento")) or _to_date(payload.get("data_transacao"))

        for i in range(total_parcelas):
            status_atual = payload.get("status", "realizado")
            dt_pagamento = _to_date(payload.get("data_pagamento"))
            
            # Lógica Opção B: Primeira parcela segue escolha, demais ficam previstas
            if i > 0:
                status_atual = "previsto"
                dt_pagamento = None
            
            vencimento_atual = data_base_venc + relativedelta(months=i) if data_base_venc else None
            
            desc = payload.get("descricao") or ""
            if total_parcelas > 1:
                desc = f"{desc} ({i+1}/{total_parcelas})".strip()

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
                desc,
                valor_parcela,
                payload.get("forma_pagamento"),
                payload.get("tipo_pagamento"),
                total_parcelas,
                _to_date(payload.get("data_transacao")),
                vencimento_atual,
                payload.get("banco"),
                payload.get("data_registro"),
                payload.get("criado_em"),
                status_atual,
                dt_pagamento
            )

async def update_transacao_to_realizado(transacao_id: int, data_pagamento: date):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE transacoes
            SET status = 'realizado',
                data_pagamento = $1
            WHERE id = $2
        """, data_pagamento, transacao_id)

async def get_pendentes_by_month(user_id: str, ano: int, mes: int):
    async with pool.acquire() as conn:
        # Busca transações que vencem no mês ou cuja transação foi no mês e estão pendentes
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

def _to_date(value) -> date | None:
    if value is None: return None
    if isinstance(value, date): return value
    if isinstance(value, str):
        try: return date.fromisoformat(value)
        except: return None
    return None

def _expand_transacao(row: dict) -> list[dict]:
    """Mantida para compatibilidade com registros antigos, mas novos ja sao criados expandidos."""
    # Como agora criamos linhas reais, esta função pode retornar apenas o registro padrão
    # mas mantemos a logica caso existam registros antigos nao migrados no seu banco.
    tipo_pag = row.get("tipo_pagamento") or "unica"
    if tipo_pag != "parcelado":
        item = dict(row)
        item["vencimento_parcela"] = _to_date(row.get("data_vencimento")) or _to_date(row.get("data_transacao"))
        item["valor_parcela"] = float(row.get("valor") or 0.0)
        return [item]
    
    # Se ja tem numero de parcela na descricao ou valor ja é a parcela, evitamos expandir de novo
    # Aqui apenas retornamos a propria linha como item unico para nao duplicar registros novos
    item = dict(row)
    item["vencimento_parcela"] = _to_date(row.get("data_vencimento")) or _to_date(row.get("data_transacao"))
    item["valor_parcela"] = float(row.get("valor") or 0.0)
    return [item]

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

    total_receitas = sum(r["valor"] for r in receitas)
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
        "ano": ano, "mes": mes, "receitas": receitas, "total_receitas": round(total_receitas, 2),
        "desp_pessoal": desp_pessoal, "desp_ambos": desp_ambos,
        "total_pessoal": round(total_pessoal, 2), "total_ambos": round(total_ambos, 2),
        "total_lancado": round(total_pessoal + total_ambos, 2),
        "meu_custo_real": round(meu_custo_real, 2), "sobra": round(sobra, 2),
        "grupos_pessoal": agrupar(desp_pessoal), "grupos_ambos": agrupar(desp_ambos),
        "grupos_receitas": agrupar(receitas, "valor")
    }

async def get_previous_balance(user_id: str, year: int, month: int) -> float:
    rec_raw, dp_raw, da_raw = await _fetch_all_transacoes(user_id)
    todas = rec_raw + dp_raw + da_raw
    if not todas: return 0.0
    datas = [d for t in todas if (d := _to_date(t.get("data_vencimento")) or _to_date(t.get("data_transacao")))]
    if not datas: return 0.0
    
    current = min(datas).replace(day=1)
    target_date = date(year, month, 1)
    saldo_acumulado = 0.0
    while current < target_date:
        resumo = await get_monthly_summary(user_id, current.year, current.month)
        saldo_acumulado += resumo["sobra"]
        current += relativedelta(months=1)
    return round(saldo_acumulado, 2)

async def get_months_with_installments():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT data_vencimento, parcelas_total FROM transacoes WHERE tipo_pagamento = 'parcelado'")
    meses = set()
    hoje = date.today().replace(day=1)
    for r in rows:
        d = _to_date(r["data_vencimento"])
        if not d: continue
        for i in range(r["parcelas_total"] or 1):
            venc = d + relativedelta(months=i)
            if venc >= hoje: meses.add((venc.year, venc.month))
    return sorted(meses)