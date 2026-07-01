import logging
from datetime import date, datetime
from calendar import month_name

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import database
import keyboards

logger = logging.getLogger(__name__)
router = Router()

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]


def fmt(valor: float) -> str:
    """Formata valor em R$ com separador de milhar e 2 casas decimais."""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _bar(valor: float, total: float, width: int = 10) -> str:
    """Barra de progresso simples."""
    if total <= 0:
        return "░" * width
    filled = round((valor / total) * width)
    return "█" * filled + "░" * (width - filled)


def build_monthly_report(data: dict, titulo_extra: str = "") -> str:
    """
    Monta a mensagem de relatório mensal completo.
    """
    mes_nome = MESES_PT[data["mes"]]
    ano = data["ano"]

    linhas = []
    linhas.append(f"📊 *RESUMO FINANCEIRO{titulo_extra}*")
    linhas.append(f"📅 {mes_nome.upper()}/{ano}")
    linhas.append("")

    # ── Saldo ──────────────────────────────────────────────
    saldo = data["sobra"]
    emoji_saldo = "🟢" if saldo >= 0 else "🔴"
    linhas.append(f"{emoji_saldo} *SALDO DO MÊS*")
    linhas.append(f"`{fmt(saldo)}`")
    linhas.append("")

    # ── Entradas ───────────────────────────────────────────
    linhas.append(f"📈 *ENTRADAS*")
    linhas.append(f"`{fmt(data['total_receitas'])}`")
    if data["grupos_receitas"]:
        for cat, val in sorted(data["grupos_receitas"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_receitas"] * 100) if data["total_receitas"] > 0 else 0
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
    else:
        linhas.append("  _Nenhuma receita registrada_")
    linhas.append("")

    # ── Saídas ─────────────────────────────────────────────
    linhas.append(f"📉 *SAÍDAS*")
    linhas.append(f"Total lançado: `{fmt(data['total_lancado'])}`")
    linhas.append(f"Seu custo real: `{fmt(data['meu_custo_real'])}`")
    linhas.append("")

    # Despesas pessoais por categoria
    if data["grupos_pessoal"]:
        linhas.append("👤 *Pessoais*")
        for cat, val in sorted(data["grupos_pessoal"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_pessoal"] * 100) if data["total_pessoal"] > 0 else 0
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
        linhas.append("")

    # Despesas compartilhadas por categoria
    if data["grupos_ambos"]:
        linhas.append("🏠 *Compartilhadas* _(50% do total)_")
        for cat, val in sorted(data["grupos_ambos"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_ambos"] * 100) if data["total_ambos"] > 0 else 0
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
        linhas.append(f"  Total casal: `{fmt(data['total_ambos'])}`")
        linhas.append(f"  Sua parte: `{fmt(data['total_ambos'] * 0.5)}`")
        linhas.append("")

    # ── Parcelas ───────────────────────────────────────────
    parcelados = [
        r for r in (data["desp_pessoal"] + data["desp_ambos"])
        if r.get("tipo_pagamento") == "parcelado"
    ]
    if parcelados:
        linhas.append("💳 *PARCELAS DO MÊS*")
        for p in parcelados:
            desc = p.get("descricao") or p.get("categoria_text") or "Sem descrição"
            total_p = p.get("parcelas_total") or "?"
            venc = p.get("data_vencimento")
            venc_str = venc.strftime("%d/%m") if venc else "-"
            escopo_icon = "🏠" if p.get("escopo") == "ambos" else "👤"
            linhas.append(
                f"  {escopo_icon} {desc.title()} — {total_p}x — venc. {venc_str} — `{fmt(float(p['valor']))}`"
            )
        linhas.append("")

    # ── Sobra líquida ──────────────────────────────────────
    linhas.append("⚖️ *SOBRA LÍQUIDA*")
    linhas.append(f"`{fmt(saldo)}`")
    linhas.append("")

    # ── Insights ───────────────────────────────────────────
    insights = _generate_insights(data)
    if insights:
        linhas.append("💡 *INSIGHTS*")
        for i in insights:
            linhas.append(i)

    return "\n".join(linhas)


def _generate_insights(data: dict) -> list[str]:
    insights = []

    if data["total_receitas"] == 0:
        insights.append("⚠️ Nenhuma receita registrada neste mês.")

    if data["total_lancado"] == 0:
        insights.append("ℹ️ Nenhuma despesa registrada neste mês.")
        return insights

    # Comprometimento da renda
    if data["total_receitas"] > 0:
        comprometimento = (data["meu_custo_real"] / data["total_receitas"]) * 100
        if comprometimento >= 90:
            insights.append(f"🔴 {comprometimento:.0f}% da sua renda está comprometida.")
        elif comprometimento >= 70:
            insights.append(f"⚠️ {comprometimento:.0f}% da sua renda está comprometida.")
        else:
            insights.append(f"✅ {comprometimento:.0f}% da sua renda comprometida. Bom controle!")

    # Categoria mais cara
    todos_grupos = {**data["grupos_pessoal"]}
    for cat, val in data["grupos_ambos"].items():
        todos_grupos[cat] = todos_grupos.get(cat, 0) + val * 0.5

    if todos_grupos:
        maior_cat = max(todos_grupos, key=todos_grupos.get)
        insights.append(f"📌 Maior gasto: *{maior_cat.title()}* com `{fmt(todos_grupos[maior_cat])}`.")

    # Saldo negativo
    if data["sobra"] < 0:
        insights.append(f"🔴 Saldo negativo de `{fmt(abs(data['sobra']))}`. Atenção!")

    return insights


# ─── Handlers ──────────────────────────────────────────────────────────────────

@router.message(F.text == "📊 Meu Relatório")
async def open_report_menu(message: Message):
    await message.answer(
        "Escolha o tipo de relatório:",
        reply_markup=keyboards.report_menu_keyboard()
    )


@router.message(F.text == "⬅️ Voltar")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Menu principal:",
        reply_markup=keyboards.main_menu()
    )


@router.message(F.text == "📅 Mês Atual")
async def report_current_month(message: Message):
    hoje = date.today()
    user_id = str(message.from_user.id)

    await message.answer("⏳ Gerando relatório...")

    data = await database.get_monthly_summary(user_id, hoje.year, hoje.month)
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

    # Meses a exibir: anterior + atual + futuros com parcelas
    meses_parcelas = await database.get_months_with_installments()

    meses = set()

    # Mês anterior
    if hoje.month == 1:
        meses.add((hoje.year - 1, 12))
    else:
        meses.add((hoje.year, hoje.month - 1))

    # Mês atual
    meses.add((hoje.year, hoje.month))

    # Meses futuros com parcelas
    for ano, mes in meses_parcelas:
        meses.add((ano, mes))

    meses_ordenados = sorted(meses)

    for ano, mes in meses_ordenados:
        data = await database.get_monthly_summary(user_id, ano, mes)

        # Meses futuros sem lançamentos mas com parcelas
        if mes > hoje.month or ano > hoje.year:
            parcelas = await database.get_installments_for_month(ano, mes)
            if parcelas and not data["desp_pessoal"] and not data["desp_ambos"]:
                data["desp_ambos"] = parcelas
                data["total_ambos"] = sum(float(p["valor"]) for p in parcelas)
                data["total_lancado"] = data["total_ambos"]
                data["meu_custo_real"] = data["total_ambos"] * 0.5
                data["sobra"] = data["total_receitas"] - data["meu_custo_real"]
                from collections import defaultdict
                grupos = defaultdict(float)
                for p in parcelas:
                    grupos[p.get("categoria_text") or "Outros"] += float(p["valor"])
                data["grupos_ambos"] = dict(grupos)

        titulo = " — PREVISTO" if (ano > hoje.year or (ano == hoje.year and mes > hoje.month)) else ""
        texto = build_monthly_report(data, titulo_extra=titulo)

        await message.answer(
            texto,
            parse_mode="Markdown",
            reply_markup=keyboards.detail_inline_keyboard(ano, mes, message.from_user.id)
        )


# ─── Callback: Ver Lançamentos ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("detail:"))
async def show_detail(callback: CallbackQuery):
    _, ano, mes, user_id = callback.data.split(":")
    ano, mes = int(ano), int(mes)

    data = await database.get_monthly_summary(str(user_id), ano, mes)

    mes_nome = MESES_PT[mes]
    linhas = [f"🔍 *LANÇAMENTOS — {mes_nome.upper()}/{ano}*", ""]

    todas = data["desp_pessoal"] + data["desp_ambos"] + data["receitas"]

    if not todas:
        linhas.append("_Nenhum lançamento encontrado._")
    else:
        # Agrupar por categoria
        grupos: dict[str, list] = {}
        for r in todas:
            cat = r.get("categoria_text") or "Outros"
            grupos.setdefault(cat, []).append(r)

        for cat, items in sorted(grupos.items()):
            linhas.append(f"📂 *{cat.title()}*")
            for item in items:
                desc = item.get("descricao") or item.get("subcategoria_text") or "-"
                val = float(item.get("valor", 0))
                escopo = item.get("escopo", "")
                tipo = item.get("tipo_pagamento", "")
                parcelas = item.get("parcelas_total")
                data_t = item.get("data_transacao")
                data_t_str = data_t.strftime("%d/%m") if data_t else "-"

                escopo_icon = "🏠" if escopo == "ambos" else "👤"
                parcela_str = f" ({parcelas}x)" if parcelas else ""
                tipo_str = f" [{tipo}]" if tipo else ""

                linhas.append(
                    f"  {escopo_icon} {data_t_str} • {desc.title()}{parcela_str}{tipo_str} — `{fmt(val)}`"
                )
            linhas.append("")

    await callback.message.answer("\n".join(linhas), parse_mode="Markdown")
    await callback.answer()