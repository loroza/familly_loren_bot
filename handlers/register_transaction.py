import logging
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
    waiting_for_category       = State()
    waiting_for_subcategory    = State()
    waiting_for_scope          = State()
    waiting_for_description    = State()
    waiting_for_amount         = State()
    waiting_for_payment_method = State()
    waiting_for_payment_type   = State()

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
        await message.answer("Menu principal:", reply_markup=keyboards.main_menu_keyboard())
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
    escopo = "pessoal" if "Pessoal" in message.text else "ambos"
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
        valor = float(message.text.replace(",", ".").replace("R$", "").strip())
        await state.update_data(valor=valor)
        await state.set_state(TransactionState.waiting_for_payment_method)
        await message.answer("Forma de pagamento:", reply_markup=keyboards.payment_method_keyboard())
    except ValueError:
        await message.answer("❌ Valor inválido. Digite apenas números. Ex: 150.50")

@router.message(StateFilter(TransactionState.waiting_for_payment_method))
async def select_payment_method(message: Message, state: FSMContext):
    await state.update_data(forma_pagamento=message.text)
    await state.set_state(TransactionState.waiting_for_payment_type)
    await message.answer("Tipo de pagamento:", reply_markup=keyboards.payment_type_keyboard())

@router.message(StateFilter(TransactionState.waiting_for_payment_type))
async def finish_transaction(message: Message, state: FSMContext):
    await state.update_data(tipo_pagamento=message.text)
    dados = await state.get_data()

    payload = {
        "telegram_user_id": str(message.from_user.id),
        "tipo":              "receita" if dados["tipo"] == "receitas" else "despesa",
        "categoria_text":    dados.get("categoria", ""),
        "subcategoria_text": dados.get("subcategoria", ""),
        "escopo":            dados.get("escopo", "pessoal"),
        "descricao":         dados.get("descricao", ""),
        "valor":             float(dados.get("valor", 0)),
        "forma_pagamento":   dados.get("forma_pagamento", ""),
        "tipo_pagamento":    dados.get("tipo_pagamento", ""),
    }

    try:
        await database.insert_transacao(payload)
        await message.answer(
            f"✅ Registrado com sucesso!\n\n"
            f"📂 {payload['categoria_text']} › {payload['subcategoria_text']}\n"
            f"💰 R$ {payload['valor']:.2f}\n"
            f"🔖 {payload['escopo'].capitalize()} | {payload['forma_pagamento']}",
            reply_markup=keyboards.main_menu_keyboard()
        )
    except Exception:
        logger.exception("Erro ao salvar transação")
        await message.answer(
            "❌ Erro ao salvar. Tente novamente.",
            reply_markup=keyboards.main_menu_keyboard()
        )

    await state.clear()