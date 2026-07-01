# handlers/reports.py
import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
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
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def build_monthly_report(data: dict, titulo_extra: str = "") -> str:
    mes_nome = MESES_PT[data["mes"]]
    ano = data["ano"]

    saldo_anterior = data.get("saldo_anterior", 0.0)
    saldo_mes = data["sobra"]
    saldo_total = saldo_anterior + saldo_mes

    linhas = []
    linhas.append(f"📊 *RESUMO FINANCEIRO{titulo_extra}*")
    linhas.append(f"📅 {mes_nome.upper()}/{ano}")
    linhas.append("")

    # Saldo acumulado
    if saldo_anterior != 0.0:
        emoji_ant = "🟢" if saldo_anterior >= 0 else "🔴"
        linhas.append(f"{emoji_ant} *Saldo Anterior:* `{fmt(saldo_anterior)}`")

    emoji_mes = "🟢" if saldo_mes >= 0 else "🔴"
    linhas.append(f"{emoji_mes} *Gerado no Mês:* `{fmt(saldo_mes)}`")

    emoji_total = "🟢" if saldo_total >= 0 else "🔴"
    linhas.append(f"{emoji_total} *SALDO ACUMULADO:* `{fmt(saldo_total)}`")
    linhas.append("")

    # Entradas
    linhas.append("📈 *ENTRADAS*")
    linhas.append(f"`{fmt(data['total_receitas'])}`")
    if data["grupos_receitas"]:
        for cat, val in sorted(data["grupos_receitas"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_receitas"] * 100) if data["total_receitas"] > 0 else 0
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
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
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
        linhas.append("")

    if data["grupos_ambos"]:
        linhas.append("🏠 *Compartilhadas* _(50% do total)_")
        for cat, val in sorted(data["grupos_ambos"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_ambos"] * 100) if data["total_ambos"] > 0 else 0
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
        linhas.append(f"  Total casal: `{fmt(data['total_ambos'])}`")
        linhas.append(f"  Sua parte: `{fmt(data['total_ambos'] * 0.5)}`")
        linhas.append("")

    # Parcelas do mês
    parcelados = [
        r for r in (data["desp_pessoal"] + data["desp_ambos"])
        if (r.get("tipo_pagamento") or "unico") == "parcelado"
    ]
    if parcelados:
        linhas.append("💳 *PARCELAS DO MÊS*")
        for p in parcelados:
            desc = p.get("descricao") or p.get("categoria_text") or "Sem descrição"
            num = p.get("numero_parcela", 1)
            total_p = p.get("parcelas_total", 1)
            venc = p.get("vencimento_parcela")
            venc_str = venc.strftime("%d/%m") if venc else "-"
            escopo_icon = "🏠" if p.get("escopo") == "ambos" else "👤"
            val_parcela = p.get("valor_parcela", 0)
            linhas.append(
                f"  {escopo_icon} ({num}/{total_p}) {desc.title()}\n        Vencimento: {venc_str} — `{fmt(val_parcela)}`"
            )
        linhas.append("")

    linhas.append("⚖️ *SOBRA LÍQUIDA DO MÊS*")
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
            insights.append(f"✅ {comprometimento:.0f}% da sua renda comprometida. Bom controle!")

    todos_grupos = {**data["grupos_pessoal"]}
    for cat, val in data["grupos_ambos"].items():
        todos_grupos[cat] = todos_grupos.get(cat, 0) + val * 0.5

    if todos_grupos:
        maior_cat = max(todos_grupos, key=todos_grupos.get)
        insights.append(f"📌 Maior gasto: *{maior_cat.title()}* com `{fmt(todos_grupos[maior_cat])}`.")

    if saldo_total is not None and saldo_total < 0:
        insights.append(f"🔴 Saldo acumulado negativo de `{fmt(abs(saldo_total))}`. Atenção!")
    elif data["sobra"] < 0:
        insights.append(f"🔴 Saldo negativo de `{fmt(abs(data['sobra']))}`. Atenção!")

    return insights


# ─── Handlers ────

@router.message(F.text == "📊 Meu Relatório")
async def open_report_menu(message: Message):
    await message.answer(
        "Escolha o tipo de relatório:",
        reply_markup=keyboards.report_menu_keyboard()
    )


@router.message(F.text == "⬅️ Voltar")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Menu principal:", reply_markup=keyboards.main_menu_keyboard())


@router.message(F.text == "📅 Mês Atual")
async def report_current_month(message: Message):
    hoje = date.today()
    user_id = str(message.from_user.id)
    await message.answer("⏳ Gerando relatório...")

    summary = await database.get_monthly_summary(user_id, hoje.year, hoje.month)
    anterior = await database.get_previous_balance(user_id, hoje.year, hoje.month)
    summary["saldo_anterior"] = anterior

    texto = build_monthly_report(summary)
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
    if hoje.month == 1:
        meses.add((hoje.year - 1, 12))
    else:
        meses.add((hoje.year, hoje.month - 1))
    meses.add((hoje.year, hoje.month))
    for ano, mes in meses_parcelas:
        meses.add((ano, mes))

    for ano, mes in sorted(meses):
        summary = await database.get_monthly_summary(user_id, ano, mes)
        anterior = await database.get_previous_balance(user_id, ano, mes)
        summary["saldo_anterior"] = anterior

        titulo = " — PREVISTO" if (ano > hoje.year or (ano == hoje.year and mes > hoje.month)) else ""
        texto = build_monthly_report(summary, titulo_extra=titulo)
        await message.answer(
            texto,
            parse_mode="Markdown",
            reply_markup=keyboards.detail_inline_keyboard(ano, mes, message.from_user.id)
        )


# ─── Callback: Ver Lançamentos ────

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
        grupos: dict[str, list] = {}
        for r in todas:
            cat = r.get("categoria_text") or "Outros"
            grupos.setdefault(cat, []).append(r)

        for cat, items in sorted(grupos.items()):
            linhas.append(f"📂 *{cat.title()}*")
            for item in items:
                desc = item.get("descricao") or item.get("subcategoria_text") or "-"
                val = item.get("valor_parcela") or float(item.get("valor", 0))
                escopo = item.get("escopo", "")
                tipo_pag = item.get("tipo_pagamento", "")
                num = item.get("numero_parcela")
                total_p = item.get("parcelas_total")
                venc = item.get("vencimento_parcela") or _to_date(item.get("data_transacao"))
                venc_str = venc.strftime("%d/%m") if venc else "-"

                escopo_icon = "🏠" if escopo == "ambos" else "👤"
                parcela_str = f" {num}/{total_p}" if tipo_pag == "parcelado" and num else ""

                linhas.append(
                    f"  {escopo_icon} {venc_str} • {desc.title()}{parcela_str} — `{fmt(val)}`"
                )
            linhas.append("")

    await callback.message.answer("\n".join(linhas), parse_mode="Markdown")
    await callback.answer()


def _to_date(value):
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