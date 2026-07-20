# handlers/reports.py
import logging
from datetime import date

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

    if saldo_anterior != 0.0:
        emoji_ant = "🟢" if saldo_anterior >= 0 else "🔴"
        linhas.append(f"{emoji_ant} *Saldo Anterior:* `{fmt(saldo_anterior)}`")

    emoji_mes = "🟢" if saldo_mes >= 0 else "🔴"
    linhas.append(f"{emoji_mes} *Gerado no Mês:* `{fmt(saldo_mes)}`")

    emoji_total = "🟢" if saldo_total >= 0 else "🔴"
    linhas.append(f"{emoji_total} *SALDO ACUMULADO:* `{fmt(saldo_total)}`")
    linhas.append("")

    linhas.append("📈 *ENTRADAS*")
    linhas.append(f"`{fmt(data['total_receitas'])}`")
    if data["grupos_receitas"]:
        for cat, val in sorted(data["grupos_receitas"].items(), key=lambda x: -x[1]):
            pct = (val / data["total_receitas"] * 100) if data["total_receitas"] > 0 else 0
            linhas.append(f"  • {cat.title()}: `{fmt(val)}` _{pct:.0f}%_")
    else:
        linhas.append("  _Nenhuma receita registrada_")
    linhas.append("")

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

    meses_por_nome = {
        "Janeiro": 1, "Fevereiro": 2, "Março": 3, "Abril": 4,
        "Maio": 5, "Junho": 6, "Julho": 7, "Agosto": 8,
        "Setembro": 9, "Outubro": 10, "Novembro": 11, "Dezembro": 12,
    }

    mes = meses_por_nome.get(texto)

    if mes is None:
        await message.answer(
            "❌ Escolha um mês usando o teclado.",
            reply_markup=keyboards.report_month_keyboard()
        )
        return

    await state.update_data(mes=mes)
    await state.set_state(ReportState.waiting_for_year)
    await message.answer(
        f"Você escolheu *{MESES_PT[mes]}*.\n\n"
        "Agora informe o ano.\n"
        "Exemplo: `2026`",
        parse_mode="Markdown"
    )


@router.message(StateFilter(ReportState.waiting_for_year))
async def select_report_year(message: Message, state: FSMContext):
    texto = (message.text or "").strip()

    if texto == "⬅️ Voltar":
        await state.set_state(ReportState.waiting_for_month)
        await message.answer(
            "📅 Selecione o mês do relatório:",
            reply_markup=keyboards.report_month_keyboard()
        )
        return

    try:
        ano = int(texto)
        if ano < 2000 or ano > 2100:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Ano inválido. Informe um ano com quatro dígitos.\n"
            "Exemplo: `2026`",
            parse_mode="Markdown"
        )
        return

    dados = await state.get_data()
    mes = dados["mes"]
    user_id = str(message.from_user.id)

    await message.answer(
        f"⏳ Gerando relatório de *{MESES_PT[mes]} de {ano}*...",
        parse_mode="Markdown"
    )

    summary = await database.get_monthly_summary(user_id, ano, mes)
    saldo_anterior = await database.get_previous_balance(user_id, ano, mes)
    summary["saldo_anterior"] = saldo_anterior

    texto_relatorio = build_monthly_report(summary)

    await message.answer(
        texto_relatorio,
        parse_mode="Markdown",
        reply_markup=keyboards.detail_inline_keyboard(ano, mes, message.from_user.id)
    )

    await state.clear()


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

    linhas = [f"🔍 *LANÇAMENTOS — {mes_nome.upper()}/{ano}*", ""]

    receitas = data["receitas"]
    despesas = data["desp_pessoal"] + data["desp_ambos"]

    if not receitas and not despesas:
        await callback.message.answer(
            "_Nenhum lançamento encontrado para este período._",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if receitas:
        linhas.append("📈 *RECEITAS*")
        linhas.extend(_format_group_hierarchy(receitas))
        linhas.append("")

    if despesas:
        linhas.append("📉 *DESPESAS*")
        linhas.extend(_format_group_hierarchy(despesas))

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
            f"_Nenhuma transação prevista para {MESES_PT[mes]}/{ano}._",
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    for item in pendentes:
        status_texto = "⏳ Previsto"
        data_venc = item.get("data_vencimento")
        data_venc_texto = str(data_venc) if data_venc else "-"

        msg = (
            f"⏳ *TRANSAÇÃO PREVISTA*\n\n"
            f"📂 {item.get('categoria_text', '-')} › {item.get('subcategoria_text', '-')}\n"
            f"💰 `R$ {float(item['valor']):.2f}`\n"
            f"🔖 Escopo: {item.get('escopo', '-')}\n"
            f"📝 Descrição: {item.get('descricao') or '-'}\n"
            f"📅 Data da transação: {str(item.get('data_transacao')) or '-'}\n"
            f"🗓️ Data de vencimento: {data_venc_texto}\n"
            f"💳 Forma de pagamento: {item.get('forma_pagamento') or '-'}\n"
            f"📦 Tipo de pagamento: {item.get('tipo_pagamento') or '-'}\n"
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

    hoje = date.today().isoformat()

    try:
        await database.update_transacao_to_realizado(transacao_id, hoje)
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ *Pagamento realizado com sucesso!*",
            parse_mode="Markdown"
        )
    except Exception:
        logger.exception("Erro ao realizar pagamento")
        await callback.message.answer(
            "❌ Erro ao atualizar o pagamento. Tente novamente."
        )

    await callback.answer()


def _format_group_hierarchy(items_list: list) -> list[str]:
    def get_date(item):
        d = item.get("vencimento_parcela") or item.get("data_transacao")
        return _to_date(d) or date(1970, 1, 1)

    sorted_items = sorted(items_list, key=get_date)
    output = []
    grouped = {}

    for item in sorted_items:
        date_str = get_date(item).strftime("%d/%m/%Y")
        cat = (item.get("categoria_text") or "Outros").title()

        if date_str not in grouped:
            grouped[date_str] = {}
        if cat not in grouped[date_str]:
            grouped[date_str][cat] = []

        grouped[date_str][cat].append(item)

    for date_str, categories in grouped.items():
        output.append(f"\n📅 *{date_str}*")

        for cat, items in categories.items():
            output.append(f"\n  📂 *{cat}*")

            for item in items:
                subcat = item.get("subcategoria_text")
                desc = (item.get("descricao") or subcat or "-").title()
                val = item.get("valor_parcela") or float(item.get("valor", 0))
                escopo = item.get("escopo", "")
                tipo_pag = item.get("tipo_pagamento", "")

                escopo_icon = "🏠" if escopo == "ambos" else "👤"

                parcela_str = ""
                if tipo_pag == "parcelado":
                    num = item.get("numero_parcela")
                    tot = item.get("parcelas_total")
                    parcela_str = f"({num}/{tot}) "

                output.append(
                    f"          {escopo_icon} `{fmt(val)}` ► {parcela_str}{desc}"
                )

    return output


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