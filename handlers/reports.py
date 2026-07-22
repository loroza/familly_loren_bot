# handlers/reports.py
import logging
import re
from datetime import date, datetime

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

import database
import keyboards

logger = logging.getLogger(__name__)
router = Router()

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]


def fmt(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except Exception:
            return None
    return None


def _get_ref_date(item: dict):
    """
    Data de referência (prioridade):
      1) data_pagamento (se existir)
      2) data_vencimento
      3) data_transacao
    Aceita variações de nomes de campo comuns.
    Retorna date ou None.
    """
    if not item:
        return None

    # Checar várias chaves possíveis para cada tipo de data
    for key in ("data_pagamento", "data_pagamento_date", "pagamento", "data_pago"):
        if item.get(key):
            d = _to_date(item.get(key))
            if d:
                return d

    for key in ("data_vencimento", "vencimento", "data_venc", "venc"):
        if item.get(key):
            d = _to_date(item.get(key))
            if d:
                return d

    for key in ("data_transacao", "transacao", "data_transacao_date", "data"):
        if item.get(key):
            d = _to_date(item.get(key))
            if d:
                return d

    # fallback to explicit computed field used by DB/expand (if present)
    if item.get("vencimento_parcela"):
        d = _to_date(item.get("vencimento_parcela"))
        if d:
            return d

    return None


def _belongs_to_month(item: dict, ano: int, mes: int) -> bool:
    """Determina se um lançamento pertence ao mês/ano do relatório.
    Regra de pertencimento (prioridade):
      1) data_transacao (transações efetivas pertencem ao mês da transação)
      2) data_pagamento (data em que foi pago)
      3) data_vencimento (vencimento / previsão)
    Retorna True se a data correspondente pertencer ao ano/mes informados.
    """
    if not item:
        return False

    # 1) data_transacao
    dt = _to_date(item.get("data_transacao") or item.get("transacao") or item.get("data_transacao_date"))
    if dt:
        return dt.year == ano and dt.month == mes

    # 2) data_pagamento
    dt = _to_date(item.get("data_pagamento") or item.get("data_pagamento_date") or item.get("pagamento") or item.get("data_pago"))
    if dt:
        return dt.year == ano and dt.month == mes

    # 3) data_vencimento / vencimento_parcela
    dt = _to_date(item.get("data_vencimento") or item.get("vencimento") or item.get("data_venc") or item.get("venc") or item.get("vencimento_parcela"))
    if dt:
        return dt.year == ano and dt.month == mes

    return False


# --- Markdown escape helper (para parse_mode="Markdown") ---
_md_esc_re = re.compile(r'([_\*\[\]\(\)`])')

def _escape_md(text: str) -> str:
    """Escapa caracteres que quebram o parse de Markdown do Telegram
    para strings dinâmicas vindas do banco.
    """
    if text is None:
        return ""
    return _md_esc_re.sub(r'\\\1', str(text))


def build_monthly_report(data: dict, titulo_extra: str = "") -> str:
    mes_nome = MESES_PT[data["mes"]]
    ano = data["ano"]

    saldo_anterior = data.get("saldo_anterior", 0.0)
    # Recalcular totais com a regra de pertencimento (priorizando data_transacao)
    # Isso corrige casos em que o banco/summary trouxe itens por vencimento em vez de por data de transação.
    receitas_raw = data.get("receitas", [])
    receitas_mes = [r for r in receitas_raw if _belongs_to_month(r, ano, data["mes"])]
    total_receitas_calc = sum((r.get("valor_parcela") or float(r.get("valor", 0) or 0)) for r in receitas_mes)
    grupos_receitas_calc: dict = {}
    for r in receitas_mes:
        cat = (r.get("categoria_text") or "Outros").strip()
        grupos_receitas_calc[cat] = grupos_receitas_calc.get(cat, 0) + (r.get("valor_parcela") or float(r.get("valor", 0) or 0))

    despesas_raw = data.get("desp_pessoal", []) + data.get("desp_ambos", [])
    despesas_mes = [r for r in despesas_raw if _belongs_to_month(r, ano, data["mes"])]
    total_lancado_calc = sum((r.get("valor_parcela") or float(r.get("valor", 0) or 0)) for r in despesas_mes)

    grupos_pessoal_calc: dict = {}
    grupos_ambos_calc: dict = {}
    total_pessoal_calc = 0.0
    total_ambos_calc = 0.0
    for r in despesas_mes:
        val = (r.get("valor_parcela") or float(r.get("valor", 0) or 0))
        cat = (r.get("categoria_text") or "Outros").strip()
        if r.get("escopo") == "ambos":
            grupos_ambos_calc[cat] = grupos_ambos_calc.get(cat, 0) + val
            total_ambos_calc += val
        else:
            grupos_pessoal_calc[cat] = grupos_pessoal_calc.get(cat, 0) + val
            total_pessoal_calc += val

    meu_custo_real_calc = total_pessoal_calc + (total_ambos_calc * 0.5)

    # Atualizar os campos do dicionário para que o restante do relatório use os valores corrigidos
    data["total_receitas"] = total_receitas_calc
    data["grupos_receitas"] = grupos_receitas_calc
    data["total_lancado"] = total_lancado_calc
    data["meu_custo_real"] = meu_custo_real_calc
    data["grupos_pessoal"] = grupos_pessoal_calc
    data["total_pessoal"] = total_pessoal_calc
    data["grupos_ambos"] = grupos_ambos_calc
    data["total_ambos"] = total_ambos_calc

    saldo_mes = total_receitas_calc - meu_custo_real_calc
    saldo_total = saldo_anterior + saldo_mes

    linhas = [f"📊 *RESUMO FINANCEIRO{_escape_md(titulo_extra)}*", f"📅 {_escape_md(mes_nome.upper())}/{ano}", ""]

    if saldo_anterior != 0.0:
        emoji_ant = "🟢" if saldo_anterior >= 0 else "🔴"
        linhas.append(f"{emoji_ant} *Saldo Anterior:* `{fmt(saldo_anterior)}`")

    emoji_mes = "🟢" if saldo_mes >= 0 else "🔴"
    linhas.append(f"{emoji_mes} *Gerado no Mês:* `{fmt(saldo_mes)}`")

    emoji_total = "🟢" if saldo_total >= 0 else "🔴"
    linhas.append(f"{emoji_total} *SALDO ACUMULADO:* `{fmt(saldo_total)}`")
    linhas.append("")

    # Fluxo de caixa (sprints)
    linhas.append("💰 *FLUXO DE CAIXA (Sprints)*")
    linhas.append(f"✅ Realizado: `{fmt(data.get('realizado_receita', 0.0))}` recebido / `{fmt(data.get('realizado_gasto', 0.0))}` pago")
    linhas.append(f"💎 *Saldo em conta:* `{fmt(data.get('saldo_atual_caixa', 0.0))}`")
    linhas.append(f"⏳ Previsto: `{fmt(data.get('previsto_receita', 0.0))}` a receber / `{fmt(data.get('previsto_gasto', 0.0))}` a gastar")
    linhas.append(f"🏁 *Projeção fim do mês:* `{fmt(data.get('saldo_projetado', 0.0))}`")
    linhas.append("")

    # Entradas
    linhas.append("📈 *ENTRADAS*")
    linhas.append(f"`{fmt(data['total_receitas'])}`")
    if data["grupos_receitas"]:
        for cat, val in sorted(data["grupos_receitas"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_receitas"] * 100) if data["total_receitas"] > 0 else 0
            linhas.append(f"  • {_escape_md(cat.title())}: `{fmt(val)}` _{pct:.0f}%_")
    else:
        linhas.append("  _Nenhuma receita registrada_")
    linhas.append("")

    # Saídas
    linhas.append("📉 *SAÍDAS*")
    linhas.append(f"Total lançado: `{fmt(data['total_lancado'])}`")
    linhas.append(f"Seu custo real: `{fmt(data['meu_custo_real'])}`")
    linhas.append("")

    if data["grupos_pessoal"]:
        linhas.append("👤 *Pessoais*")
        for cat, val in sorted(data["grupos_pessoal"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_pessoal"] * 100) if data["total_pessoal"] > 0 else 0
            linhas.append(f"  • {_escape_md(cat.title())}: `{fmt(val)}` _{pct:.0f}%_")
        linhas.append("")

    if data["grupos_ambos"]:
        linhas.append("🏠 *Compartilhadas* _(50% do total)_")
        for cat, val in sorted(data["grupos_ambos"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_ambos"] * 100) if data["total_ambos"] > 0 else 0
            linhas.append(f"  • {_escape_md(cat.title())}: `{fmt(val)}` _{pct:.0f}%_")
        linhas.append(f"  Total casal: `{fmt(data['total_ambos'])}`")
        linhas.append(f"  Sua parte: `{fmt(data['total_ambos'] * 0.5)}`")
        linhas.append("")

    # Parcelas do mês (manter bloco informativo)
    parcelados = [
        r for r in (data.get("desp_pessoal", []) + data.get("desp_ambos", []))
        if (r.get("tipo_pagamento") or "unico") == "parcelado"
    ]
    # Filtrar apenas as parcelas cuja data de referência pertence ao mês/ano do relatório
    def _is_in_report_month(item):
        d = _get_ref_date(item)
        if not d:
            return False
        return d.year == data["ano"] and d.month == data["mes"]

    parcelados = [p for p in parcelados if _is_in_report_month(p)]
    if parcelados:
        linhas.append("💳 *PARCELAS DO MÊS*")
        parcelados_sorted = sorted(parcelados, key=lambda r: _get_ref_date(r) or date(1970, 1, 1))
        for p in parcelados_sorted:
            desc_raw = p.get("descricao") or p.get("categoria_text") or "Sem descrição"
            desc = _escape_md(desc_raw)
            num = p.get("numero_parcela", 1)
            total_p = p.get("parcelas_total", 1)
            venc = _to_date(p.get("data_vencimento") or p.get("vencimento") or p.get("data_venc") or p.get("venc") or p.get("vencimento_parcela"))
            venc_str = venc.strftime("%d/%m") if venc else "-"
            escopo_icon = "🏠" if p.get("escopo") == "ambos" else "👤"
            val_parcela = p.get("valor_parcela") or float(p.get("valor", 0) or 0)
            linhas.append(f"  {escopo_icon} {venc_str} • {desc} — `{fmt(val_parcela)}`")
        linhas.append("")

    linhas.append("⚖️ *SOBRA LÍQUIDA*")
    linhas.append(f"`{fmt(saldo_mes)}`")
    linhas.append("")

    insights = _generate_insights(data, saldo_total)
    if insights:
        linhas.append("💡 *INSIGHTS*")
        for i in insights:
            linhas.append(i)

    return "\n".join(linhas)


def _generate_insights(data: dict, saldo_total: float = None) -> list[str]:
    insights = []

    if data["total_receitas"] == 0:
        insights.append("⚠️ Nenhuma receita registrada neste mês.")

    if data["total_lancado"] == 0:
        insights.append("ℹ️ Nenhuma despesa registrada neste mês.")
        return insights

    if data["total_receitas"] > 0:
        comprometimento = (data["meu_custo_real"] / data["total_receitas"]) * 100
        if comprometimento >= 90:
            insights.append(f"🔴 {comprometimento:.0f}% da sua renda está comprometida.")
        elif comprometimento >= 70:
            insights.append(f"⚠️ {comprometimento:.0f}% da sua renda está comprometida.")
        else:
            insights.append(f"✅ {comprometimento:.0f}% da renda comprometida. Bom controle!")

    todos_grupos = {**data.get("grupos_pessoal", {})}
    for cat, val in data.get("grupos_ambos", {}).items():
        todos_grupos[cat] = todos_grupos.get(cat, 0) + val * 0.5

    if todos_grupos:
        maior_cat = max(todos_grupos, key=todos_grupos.get)
        insights.append(f"📌 Maior gasto: *{_escape_md(maior_cat.title())}* com `{fmt(todos_grupos[maior_cat])}`.")

    if saldo_total is not None and saldo_total < 0:
        insights.append(f"🔴 Saldo acumulado negativo de `{fmt(abs(saldo_total))}`. Atenção!")
    elif data.get("sobra", 0) < 0:
        insights.append(f"🔴 Saldo negativo de `{fmt(abs(data.get('sobra', 0)))}`. Atenção!")

    return insights


class ReportState(StatesGroup):
    waiting_for_month = State()
    waiting_for_year = State()


# ─── Menu de Relatórios ───

@router.message(F.text == "📊 Meu Relatório")
async def open_report_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Escolha o tipo de relatório:",
        reply_markup=keyboards.report_menu_keyboard()
    )


@router.message(F.text == "📅 Mensal")
async def start_monthly_report(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ReportState.waiting_for_month)
    await message.answer(
        "📅 Selecione o mês do relatório:",
        reply_markup=keyboards.report_month_keyboard()
    )


@router.message(StateFilter(ReportState.waiting_for_month))
async def select_report_month(message: Message, state: FSMContext):
    texto = (message.text or "").strip()

    if texto == "⬅️ Voltar":
        await state.clear()
        await message.answer(
            "Menu de relatórios:",
            reply_markup=keyboards.report_menu_keyboard()
        )
        return

    meses_por_nome = {m: i for i, m in enumerate(MESES_PT) if m}
    mes = meses_por_nome.get(texto)

    if mes is None:
        await message.answer(
            "❌ Escolha um mês usando o teclado.",
            reply_markup=keyboards.report_month_keyboard()
        )
        return

    await state.update_data(mes=mes)
    await state.set_state(ReportState.waiting_for_year)
    await message.answer(f"Você escolheu *{MESES_PT[mes]}*.\n\nAgora informe o ano (Ex: `2026`)", parse_mode="Markdown")


@router.message(StateFilter(ReportState.waiting_for_year))
async def select_report_year(message: Message, state: FSMContext):
    texto = (message.text or "").strip()
    if texto == "⬅️ Voltar":
        await state.set_state(ReportState.waiting_for_month)
        await message.answer("📅 Selecione o mês do relatório:", reply_markup=keyboards.report_month_keyboard())
        return

    try:
        ano = int(texto)
        if ano < 2000 or ano > 2100:
            raise ValueError
    except ValueError:
        await message.answer("❌ Ano inválido. Exemplo: `2026`", parse_mode="Markdown")
        return

    dados = await state.get_data()
    mes, user_id = dados["mes"], str(message.from_user.id)

    await message.answer(f"⏳ Gerando relatório de *{MESES_PT[mes]} de {ano}*...", parse_mode="Markdown")

    summary = await database.get_monthly_summary(user_id, ano, mes)
    summary["saldo_anterior"] = await database.get_previous_balance(user_id, ano, mes)

    await message.answer(
        build_monthly_report(summary),
        parse_mode="Markdown",
        reply_markup=keyboards.detail_inline_keyboard(ano, mes, message.from_user.id)
    )
    await state.clear()


@router.message(F.text == "📅 Mês Atual")
async def report_current_month(message: Message):
    hoje = date.today()
    user_id = str(message.from_user.id)
    await message.answer("⏳ Gerando relatório...")
    data = await database.get_monthly_summary(user_id, hoje.year, hoje.month)
    data["saldo_anterior"] = await database.get_previous_balance(user_id, hoje.year, hoje.month)
    texto = build_monthly_report(data)
    await message.answer(
        texto,
        parse_mode="Markdown",
        reply_markup=keyboards.detail_inline_keyboard(hoje.year, hoje.month, message.from_user.id)
    )


@router.message(F.text == "📆 Controle Mensal")
async def report_monthly_control(message: Message):
    hoje = date.today()
    user_id = str(message.from_user.id)
    await message.answer("⏳ Gerando controle mensal...")

    meses_parcelas = await database.get_months_with_installments()

    meses = set()
    # inclui mês anterior e mês atual
    if hoje.month == 1:
        meses.add((hoje.year - 1, 12))
    else:
        meses.add((hoje.year, hoje.month - 1))
    meses.add((hoje.year, hoje.month))

    for ano, mes in meses_parcelas:
        meses.add((ano, mes))

    for ano, mes in sorted(meses):
        data = await database.get_monthly_summary(user_id, ano, mes)
        data["saldo_anterior"] = await database.get_previous_balance(user_id, ano, mes)
        titulo = " — PREVISTO" if (ano > hoje.year or (ano == hoje.year and mes > hoje.month)) else ""
        texto = build_monthly_report(data, titulo_extra=titulo)
        await message.answer(
            texto,
            parse_mode="Markdown",
            reply_markup=keyboards.detail_inline_keyboard(ano, mes, message.from_user.id)
        )


@router.message(F.text == "⬅️ Voltar")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Menu principal:",
        reply_markup=keyboards.main_menu_keyboard()
    )


# ─── Callback: Ver Lançamentos ───

@router.callback_query(F.data.startswith("detail:"))
async def show_detail(callback: CallbackQuery):
    _, ano, mes, user_id = callback.data.split(":")
    ano, mes = int(ano), int(mes)

    data = await database.get_monthly_summary(str(user_id), ano, mes)
    mes_nome = MESES_PT[mes]

    linhas = [f"🔍 *LANÇAMENTOS — {_escape_md(mes_nome.upper())}/{ano}*", ""]

    # Separar despesas e receitas e filtrar por pertencimento ao mês do relatório
    despesas = (data.get("desp_pessoal", []) + data.get("desp_ambos", []))
    receitas = data.get("receitas", [])

    despesas = [r for r in despesas if _belongs_to_month(r, ano, mes)]
    receitas = [r for r in receitas if _belongs_to_month(r, ano, mes)]

    if not despesas and not receitas:
        linhas.append("_Nenhum lançamento encontrado para este período._")
    else:
        # Receitas
        if receitas:
            linhas.append("📥 *RECEITAS*")
            grupos_rec: dict[str, list] = {}
            for r in receitas:
                cat = (r.get("categoria_text") or "Outros").strip()
                grupos_rec.setdefault(cat, []).append(r)

            for cat in sorted(grupos_rec.keys()):
                linhas.append(f"📂 *{_escape_md(cat.title())}*")
                items = sorted(grupos_rec[cat], key=lambda x: _to_date(x.get("data_transacao")) or _get_ref_date(x) or date(1970, 1, 1))
                for item in items:
                    desc = _escape_md(item.get("descricao") or item.get("subcategoria_text") or "-")
                    val = item.get("valor_parcela") or float(item.get("valor", 0) or 0)
                    data_ref = _to_date(item.get("data_transacao")) or _get_ref_date(item)
                    data_str = data_ref.strftime("%d/%m/%Y") if data_ref else "-"
                    linhas.append(f"  🧾 {data_str} • {desc} — `{fmt(val)}`")
                linhas.append("")

        # Despesas
        if despesas:
            linhas.append("📤 *DESPESAS*")
            grupos_desp: dict[str, list] = {}
            for r in despesas:
                cat = (r.get("categoria_text") or "Outros").strip()
                grupos_desp.setdefault(cat, []).append(r)

            for cat in sorted(grupos_desp.keys()):
                linhas.append(f"📂 *{_escape_md(cat.title())}*")
                items = sorted(grupos_desp[cat], key=lambda x: _to_date(x.get("data_transacao")) or _get_ref_date(x) or date(1970, 1, 1))
                for item in items:
                    desc = _escape_md(item.get("descricao") or item.get("subcategoria_text") or "-")
                    val = item.get("valor_parcela") or float(item.get("valor", 0) or 0)
                    escopo_icon = "🏠" if item.get("escopo") == "ambos" else "👤"
                    data_ref = _to_date(item.get("data_transacao")) or _get_ref_date(item)
                    data_str = data_ref.strftime("%d/%m/%Y") if data_ref else "-"
                    parcela_str = ""
                    if (item.get("tipo_pagamento") or "") == "parcelado":
                        num = item.get("numero_parcela")
                        tot = item.get("parcelas_total")
                        parcela_str = f"({num}/{tot}) " if num and tot else ""
                    linhas.append(f"  {escopo_icon} {data_str} • {parcela_str}{desc} — `{fmt(val)}`")
                linhas.append("")

    texto_final = "\n".join(linhas)
    if len(texto_final) > 4000:
        texto_final = texto_final[:3900] + "\n\n...(Relatório muito longo, exibindo apenas o início)"

    await callback.message.answer(texto_final, parse_mode="Markdown")
    await callback.answer()


# ─── Callback: Pendentes ───

@router.callback_query(F.data.startswith("pending:"))
async def show_pending(callback: CallbackQuery):
    _, ano, mes, user_id = callback.data.split(":")
    ano, mes = int(ano), int(mes)

    pendentes = await database.get_pendentes_by_month(str(user_id), ano, mes)

    if not pendentes:
        await callback.message.answer(
            f"_Nenhuma transação prevista para {_escape_md(MESES_PT[mes])}/{ano}._",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    # Mostrar pendentes; ordenar por data de vencimento/transação
    pendentes_sorted = sorted(pendentes, key=lambda r: _get_ref_date(r) or _to_date(r.get("data_vencimento")) or date(1970, 1, 1))
    for item in pendentes_sorted:
        status_texto = "⏳ Previsto"
        data_venc = _get_ref_date(item) or _to_date(item.get("data_vencimento"))
        data_venc_texto = data_venc.strftime("%d/%m/%Y") if data_venc else "-"

        msg = (
            f"⏳ *TRANSAÇÃO PREVISTA*\n\n"
            f"📂 {_escape_md(item.get('categoria_text', '-'))} › {_escape_md(item.get('subcategoria_text', '-'))}\n"
            f"💰 `{fmt(float(item.get('valor') or 0))}`\n"
            f"🔖 Escopo: {_escape_md(item.get('escopo', '-'))}\n"
            f"📝 Descrição: {_escape_md(item.get('descricao') or '-')}\n"
            f"📅 Data da transação: {_escape_md(str(item.get('data_transacao')) or '-')}\n"
            f"🗓️ Data de referência: {_escape_md(data_venc_texto)}\n"
            f"💳 Forma de pagamento: {_escape_md(item.get('forma_pagamento') or '-')}\n"
            f"📦 Tipo de pagamento: {_escape_md(item.get('tipo_pagamento') or '-')}\n"
            f"📌 Status: {status_texto}\n"
        )

        await callback.message.answer(
            msg,
            parse_mode="Markdown",
            reply_markup=keyboards.realizar_pagamento_inline_keyboard(item["id"])
        )

    await callback.answer()


# ─── Callback: Realizar Pagamento ───

@router.callback_query(F.data.startswith("realizar:"))
async def realizar_pagamento(callback: CallbackQuery):
    _, transacao_id = callback.data.split(":")
    transacao_id = int(transacao_id)

    hoje = date.today()

    try:
        await database.update_transacao_to_realizado(transacao_id, hoje)
        # Tentar editar a mensagem original para incluir confirmação
        try:
            # Não forçar parse_mode aqui para evitar reparse de entidades já presentes na mensagem
            await callback.message.edit_text((callback.message.text or "") + "\n\n✅ Pagamento realizado com sucesso!")
        except Exception:
            # se não for possível editar (mensagem muito antiga, etc.), apenas enviar nova mensagem
            await callback.message.answer("✅ *Pagamento realizado com sucesso!*", parse_mode="Markdown")
    except Exception:
        logger.exception("Erro ao realizar pagamento")
        await callback.message.answer("❌ Erro ao atualizar o pagamento. Tente novamente.")

    await callback.answer()


# ─── Formatação de listagem (usada no detalhe e no resumo) ───

def _format_group_hierarchy(items_list: list) -> list[str]:
    """
    Retorna linhas formatadas agrupadas por data (data de referência) e categorias.
    Ordena por data de referência (data_pagamento > data_vencimento > data_transacao).
    """
    # Ordena por data de referência
    sorted_items = sorted(items_list, key=lambda r: _get_ref_date(r) or date(1970, 1, 1))
    output = []
    grouped = {}

    for item in sorted_items:
        d = _get_ref_date(item) or _to_date(item.get("data_transacao"))
        date_str = d.strftime("%d/%m/%Y") if d else "Sem Data"
        cat = (item.get("categoria_text") or "Outros").title()

        if date_str not in grouped: grouped[date_str] = {}
        if cat not in grouped[date_str]: grouped[date_str][cat] = []
        grouped[date_str][cat].append(item)

    for date_str, categories in grouped.items():
        output.append(f"\n📅 *{_escape_md(date_str)}*")
        for cat, items in categories.items():
            output.append(f"\n  📂 *{_escape_md(cat)}*")
            for item in items:
                desc = _escape_md((item.get("descricao") or item.get("subcategoria_text") or "-").title())
                val = item.get("valor_parcela") or float(item.get("valor", 0) or 0)
                escopo_icon = "🏠" if item.get("escopo") == "ambos" else "👤"
                parcela_str = ""
                if (item.get("tipo_pagamento") or "") == "parcelado":
                    num = item.get("numero_parcela")
                    tot = item.get("parcelas_total")
                    parcela_str = f"({num}/{tot}) " if num and tot else ""
                output.append(f"          {escopo_icon} `{fmt(val)}` ► {parcela_str}{desc}")
    return output