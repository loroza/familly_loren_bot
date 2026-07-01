import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

import keyboards
import database

logger = logging.getLogger(__name__)
router = Router()


class TransactionState(StatesGroup):
    waiting_for_category = State()
    waiting_for_subcategory = State()
    waiting_for_scope = State()
    waiting_for_description = State()
    waiting_for_amount = State()
    waiting_for_transaction_date = State()
    waiting_for_due_date = State()
    waiting_for_payment_method = State()
    waiting_for_payment_type = State()
    waiting_for_installments = State()


def parse_date_to_iso(date_text: str):
    text = (date_text or "").strip()

    if text in [".", "-", ""]:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue

    return "INVALID"


def normalize_scope(text: str) -> str:
    if "Pessoal" in text:
        return "pessoal"
    if "Ambos" in text:
        return "ambos"
    return ""


def normalize_payment_type(text: str) -> str:
    if "Parcelado" in text:
        return "parcelado"
    if "À Vista" in text or "A Vista" in text:
        return "avista"
    return text.strip()


async def save_transaction(message: Message, state: FSMContext):
    dados = await state.get_data()

    payload = {
        "telegram_user_id": str(message.from_user.id),
        "tipo": "receita" if dados["tipo"] == "receitas" else "despesa",
        "categoria_text": dados.get("categoria", ""),
        "subcategoria_text": dados.get("subcategoria", ""),
        "escopo": dados.get("escopo", "pessoal"),
        "descricao": dados.get("descricao", ""),
        "valor": float(dados.get("valor", 0)),
        "forma_pagamento": dados.get("forma_pagamento", ""),
        "tipo_pagamento": dados.get("tipo_pagamento", ""),
        "parcelas_total": dados.get("parcelas_total"),
        "data_transacao": dados.get("data_transacao"),
        "data_vencimento": dados.get("data_vencimento"),
        "banco": None,
    }

    try:
        await database.insert_transacao(payload)

        parcelas_texto = (
            str(payload["parcelas_total"])
            if payload["parcelas_total"] is not None
            else "-"
        )

        data_transacao_texto = payload["data_transacao"] or "-"
        data_vencimento_texto = payload["data_vencimento"] or "-"

        await message.answer(
            f"✅ Registrado com sucesso!\n\n"
            f"📂 {payload['categoria_text']} › {payload['subcategoria_text']}\n"
            f"💰 R$ {payload['valor']:.2f}\n"
            f"🔖 Escopo: {payload['escopo']}\n"
            f"📝 Descrição: {payload['descricao'] or '-'}\n"
            f"📅 Data da transação: {data_transacao_texto}\n"
            f"🗓️ Data de vencimento: {data_vencimento_texto}\n"
            f"💳 Forma de pagamento: {payload['forma_pagamento'] or '-'}\n"
            f"📦 Tipo de pagamento: {payload['tipo_pagamento'] or '-'}\n"
            f"🔢 Parcelas: {parcelas_texto}",
            reply_markup=keyboards.main_menu_keyboard()
        )

    except Exception:
        logger.exception("Erro ao salvar transação")
        await message.answer(
            "❌ Erro ao salvar a transação. Tente novamente.",
            reply_markup=keyboards.main_menu_keyboard()
        )

    await state.clear()


@router.message(F.text == "➖ Nova Despesa")
async def start_expense(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(tipo="despesas")
    await state.set_state(TransactionState.waiting_for_category)
    await message.answer(
        "Selecione a categoria:",
        reply_markup=keyboards.get_main_category_keyboard("despesas")
    )


@router.message(F.text == "➕ Nova Receita")
async def start_income(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(tipo="receitas")
    await state.set_state(TransactionState.waiting_for_category)
    await message.answer(
        "Selecione a categoria:",
        reply_markup=keyboards.get_main_category_keyboard("receitas")
    )


@router.message(StateFilter(TransactionState.waiting_for_category))
async def select_category(message: Message, state: FSMContext):
    if message.text == "⬅️ Voltar":
        await state.clear()
        await message.answer(
            "Menu principal:",
            reply_markup=keyboards.main_menu_keyboard()
        )
        return

    data = await state.get_data()
    await state.update_data(categoria=message.text)
    await state.set_state(TransactionState.waiting_for_subcategory)
    await message.answer(
        "Selecione a subcategoria:",
        reply_markup=keyboards.get_subcategory_keyboard(data["tipo"], message.text)
    )


@router.message(StateFilter(TransactionState.waiting_for_subcategory))
async def select_subcategory(message: Message, state: FSMContext):
    if message.text == "⬅️ Voltar":
        data = await state.get_data()
        await state.set_state(TransactionState.waiting_for_category)
        await message.answer(
            "Selecione a categoria:",
            reply_markup=keyboards.get_main_category_keyboard(data["tipo"])
        )
        return

    await state.update_data(subcategoria=message.text)
    await state.set_state(TransactionState.waiting_for_scope)
    await message.answer(
        "A transação é pessoal ou para ambos?",
        reply_markup=keyboards.scope_keyboard()
    )


@router.message(StateFilter(TransactionState.waiting_for_scope))
async def select_scope(message: Message, state: FSMContext):
    escopo = normalize_scope(message.text or "")

    if not escopo:
        await message.answer("❌ Escolha uma opção do teclado: 👤 Pessoal ou 🏠 Ambos.")
        return

    await state.update_data(escopo=escopo)
    await state.set_state(TransactionState.waiting_for_description)
    await message.answer("Digite uma descrição (ou '.' para pular):")


@router.message(StateFilter(TransactionState.waiting_for_description))
async def enter_description(message: Message, state: FSMContext):
    desc = "" if message.text.strip() == "." else message.text.strip()
    await state.update_data(descricao=desc)
    await state.set_state(TransactionState.waiting_for_amount)
    await message.answer("Qual o valor? (Ex: 150.50)")


@router.message(StateFilter(TransactionState.waiting_for_amount))
async def enter_amount(message: Message, state: FSMContext):
    try:
        valor = float(
            message.text.replace(",", ".").replace("R$", "").replace(" ", "").strip()
        )
        await state.update_data(valor=valor)
        await state.set_state(TransactionState.waiting_for_transaction_date)
        await message.answer(
            "Digite a data da transação.\n"
            "Formato: DD/MM/AAAA ou AAAA-MM-DD\n"
            "Envie '.' para deixar em branco."
        )
    except ValueError:
        await message.answer("❌ Valor inválido. Digite apenas números. Ex: 150.50")


@router.message(StateFilter(TransactionState.waiting_for_transaction_date))
async def enter_transaction_date(message: Message, state: FSMContext):
    data_transacao = parse_date_to_iso(message.text)

    if data_transacao == "INVALID":
        await message.answer(
            "❌ Data inválida.\nUse DD/MM/AAAA ou AAAA-MM-DD.\nOu envie '.' para pular."
        )
        return

    await state.update_data(data_transacao=data_transacao)
    await state.set_state(TransactionState.waiting_for_due_date)
    await message.answer(
        "Digite a data de vencimento.\n"
        "Formato: DD/MM/AAAA ou AAAA-MM-DD\n"
        "Envie '.' para deixar em branco."
    )


@router.message(StateFilter(TransactionState.waiting_for_due_date))
async def enter_due_date(message: Message, state: FSMContext):
    data_vencimento = parse_date_to_iso(message.text)

    if data_vencimento == "INVALID":
        await message.answer(
            "❌ Data inválida.\nUse DD/MM/AAAA ou AAAA-MM-DD.\nOu envie '.' para pular."
        )
        return

    await state.update_data(data_vencimento=data_vencimento)
    await state.set_state(TransactionState.waiting_for_payment_method)
    await message.answer(
        "Forma de pagamento:",
        reply_markup=keyboards.payment_method_keyboard()
    )


@router.message(StateFilter(TransactionState.waiting_for_payment_method))
async def select_payment_method(message: Message, state: FSMContext):
    valid_options = [
        "💳 Cartão de Crédito",
        "💸 Pix / Dinheiro",
        "📄 Boleto",
        "🔄 Débito Automático",
    ]

    if message.text not in valid_options:
        await message.answer("❌ Escolha uma opção do teclado para a forma de pagamento.")
        return

    await state.update_data(forma_pagamento=message.text)
    await state.set_state(TransactionState.waiting_for_payment_type)
    await message.answer(
        "Tipo de pagamento:",
        reply_markup=keyboards.payment_type_keyboard()
    )


@router.message(StateFilter(TransactionState.waiting_for_payment_type))
async def select_payment_type(message: Message, state: FSMContext):
    valid_options = [
        "📦 À Vista",
        "🗓️ Parcelado",
    ]

    if message.text not in valid_options:
        await message.answer("❌ Escolha uma opção do teclado para o tipo de pagamento.")
        return

    tipo_pagamento = normalize_payment_type(message.text)
    await state.update_data(tipo_pagamento=tipo_pagamento)

    if tipo_pagamento == "parcelado":
        await state.set_state(TransactionState.waiting_for_installments)
        await message.answer("Digite a quantidade de parcelas (ex: 6):")
        return

    await state.update_data(parcelas_total=None)
    await save_transaction(message, state)


@router.message(StateFilter(TransactionState.waiting_for_installments))
async def enter_installments(message: Message, state: FSMContext):
    try:
        parcelas = int(message.text.strip())

        if parcelas <= 1:
            await message.answer("❌ Para parcelado, informe um número maior que 1.")
            return

        await state.update_data(parcelas_total=parcelas)
        await save_transaction(message, state)

    except ValueError:
        await message.answer("❌ Quantidade inválida. Digite um número inteiro, ex: 6.")