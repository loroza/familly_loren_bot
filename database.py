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
    """
    Insere transações no banco, tratando parcelamento real (Opção B).
    Cria uma linha por parcela. A primeira parcela segue o 'status' informado,
    as demais ficam com status = 'previsto'.
    """
    async with pool.acquire() as conn:
        tipo_pag = payload.get("tipo_pagamento")
        total_parcelas = int(payload.get("parcelas_total") or 1)
        if tipo_pag != "parcelado":
            total_parcelas = 1

        valor_total = float(payload.get("valor") or 0.0)
        # Ajuste para evitar soma com erro por arredondamento:
        valor_parcela = round(valor_total / total_parcelas, 2)

        data_base_venc = _to_date(payload.get("data_vencimento")) or _to_date(payload.get("data_transacao"))

        for i in range(total_parcelas):
            # status da parcela atual e data_pagamento
            status_atual = payload.get("status", "realizado")
            dt_pagamento = _to_date(payload.get("data_pagamento"))

            # Para parcelas além da primeira, sempre previsto
            if i > 0:
                status_atual = "previsto"
                dt_pagamento = None

            vencimento_atual = (data_base_venc + relativedelta(months=i)) if data_base_venc else None

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
        rows = await conn.fetch("""
            SELECT * FROM transacoes
            WHERE telegram_user_id = $1
              AND status = 'previsto'
              AND (
                  (data_vencimento IS NOT NULL AND EXTRACT(YEAR FROM data_vencimento) = $2 AND EXTRACT(MONTH FROM data_vencimento) = $3)
                  OR
                  (data_vencimento IS NULL AND EXTRACT(YEAR FROM data_transacao) = $2 AND EXTRACT(MONTH FROM data_transacao) = $3)
              )
            ORDER BY COALESCE(data_vencimento, data_transacao)
        """, str(user_id), ano, mes)
    return [dict(r) for r in rows]


def _to_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            # aceita strings ISO completas ou apenas data
            return date.fromisoformat(value[:10])
        except Exception:
            return None
    return None


# Helpers usados pelo relatório mensal

async def _fetch_all_transacoes(telegram_user_id: str):
    async with pool.acquire() as conn:
        receitas = await conn.fetch(
            "SELECT * FROM transacoes WHERE telegram_user_id = $1 AND tipo = 'receita'", str(telegram_user_id)
        )
        desp_pessoal = await conn.fetch(
            "SELECT * FROM transacoes WHERE telegram_user_id = $1 AND tipo = 'despesa' AND escopo = 'pessoal'", str(telegram_user_id)
        )
        desp_ambos = await conn.fetch(
            "SELECT * FROM transacoes WHERE tipo = 'despesa' AND escopo = 'ambos'"
        )
    return [dict(r) for r in receitas], [dict(r) for r in desp_pessoal], [dict(r) for r in desp_ambos]


def _expand_transacao(row: dict) -> list[dict]:
    """
    Mantida por compatibilidade com dados antigos; como agora criamos registros
    reais por parcela, retornamos a própria linha com campos adicionais.
    """
    item = dict(row)
    # normaliza data de vencimento da parcela
    item["vencimento_parcela"] = _to_date(row.get("data_vencimento")) or _to_date(row.get("data_transacao"))
    try:
        item["valor_parcela"] = float(row.get("valor") or 0.0)
    except Exception:
        item["valor_parcela"] = 0.0
    return [item]


async def get_monthly_summary(telegram_user_id: str, ano: int, mes: int) -> dict:
    """
    Retorna um dicionário com campos necessários para o relatório mensal,
    incluindo os valores do fluxo de caixa (realizado / previsto).
    """
    rec_raw, dp_raw, da_raw = await _fetch_all_transacoes(telegram_user_id)

    # Filtra por mês/ano considerando data_vencimento quando disponível
    def filtrar_por_mes(rows):
        out = []
        for r in rows:
            d = _to_date(r.get("data_vencimento")) or _to_date(r.get("data_transacao"))
            if d and d.year == ano and d.month == mes:
                out.append(r)
        return out

    receitas = filtrar_por_mes(rec_raw)
    desp_pessoal = filtrar_por_mes(dp_raw)
    desp_ambos = filtrar_por_mes(da_raw)

    # Fluxo de caixa: separado por status
    realizado_receita = sum(float(r.get("valor") or 0.0) for r in receitas if (r.get("status") or "realizado") == "realizado")
    previsto_receita = sum(float(r.get("valor") or 0.0) for r in receitas if (r.get("status") or "") == "previsto")

    # Para despesas 'ambos' consideramos 50% como sua parte
    realizado_gasto_pessoal = sum(float(r.get("valor") or 0.0) for r in desp_pessoal if (r.get("status") or "") == "realizado")
    realizado_gasto_ambos = sum(float(r.get("valor") or 0.0) * 0.5 for r in desp_ambos if (r.get("status") or "") == "realizado")
    realizado_gasto = realizado_gasto_pessoal + realizado_gasto_ambos

    previsto_gasto_pessoal = sum(float(r.get("valor") or 0.0) for r in desp_pessoal if (r.get("status") or "") == "previsto")
    previsto_gasto_ambos = sum(float(r.get("valor") or 0.0) * 0.5 for r in desp_ambos if (r.get("status") or "") == "previsto")
    previsto_gasto = previsto_gasto_pessoal + previsto_gasto_ambos

    # Totais para compatibilidade com relatório existente
    total_receitas = sum(float(r.get("valor") or 0.0) for r in receitas)
    total_pessoal = sum(float(r.get("valor") or 0.0) for r in desp_pessoal)
    total_ambos = sum(float(r.get("valor") or 0.0) for r in desp_ambos)

    meu_custo_real = total_pessoal + (total_ambos * 0.5)
    sobra = total_receitas - meu_custo_real

    def agrupar(rows, campo_valor="valor"):
        grupos = {}
        for r in rows:
            cat = (r.get("categoria_text") or "Outros").strip()
            grupos[cat] = grupos.get(cat, 0.0) + float(r.get(campo_valor, 0) or 0.0)
        return grupos

    saldo_atual_caixa = realizado_receita - realizado_gasto
    saldo_projetado = (realizado_receita + previsto_receita) - (realizado_gasto + previsto_gasto)

    return {
        "ano": ano,
        "mes": mes,
        "receitas": receitas,
        "desp_pessoal": [item for r in desp_pessoal for item in _expand_transacao(r)],
        "desp_ambos": [item for r in desp_ambos for item in _expand_transacao(r)],
        "total_receitas": round(total_receitas, 2),
        "total_pessoal": round(total_pessoal, 2),
        "total_ambos": round(total_ambos, 2),
        "total_lancado": round(total_pessoal + total_ambos, 2),
        "meu_custo_real": round(meu_custo_real, 2),
        "sobra": round(sobra, 2),
        "grupos_pessoal": agrupar(desp_pessoal, "valor"),
        "grupos_ambos": agrupar(desp_ambos, "valor"),
        "grupos_receitas": agrupar(receitas, "valor"),
        # fluxo de caixa
        "realizado_receita": round(realizado_receita, 2),
        "realizado_gasto": round(realizado_gasto, 2),
        "previsto_receita": round(previsto_receita, 2),
        "previsto_gasto": round(previsto_gasto, 2),
        "saldo_atual_caixa": round(saldo_atual_caixa, 2),
        "saldo_projetado": round(saldo_projetado, 2),
    }


async def get_previous_balance(user_id: str, year: int, month: int) -> float:
    """
    Calcula um saldo acumulado até o mês anterior (simplificado).
    """
    rec_raw, dp_raw, da_raw = await _fetch_all_transacoes(user_id)
    todas = rec_raw + dp_raw + da_raw
    if not todas:
        return 0.0

    # calcula somando todas as transações com data menor que o primeiro dia do target
    target = date(year, month, 1)
    saldo = 0.0
    for r in todas:
        d = _to_date(r.get("data_vencimento")) or _to_date(r.get("data_transacao"))
        if not d or d >= target:
            continue
        val = float(r.get("valor") or 0.0)
        if r.get("tipo") == "receita":
            saldo += val
        else:
            if r.get("escopo") == "ambos":
                saldo -= val * 0.5
            else:
                saldo -= val
    return round(saldo, 2)