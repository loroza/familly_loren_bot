# handlers/cadastro.py

import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

import database
import keyboards

logger = logging.getLogger(__name__)
router = Router()


class CadastroState(StatesGroup):
    viewing_registration_menu = State()
    viewing_card_menu = State()

    waiting_for_card_name = State()
    waiting_for_card_limit = State()
    waiting_for_card_closing_day = State()
    waiting_for_card_due_day = State()


def is_back_command(message: Message) -> bool:
    return (message.text or "").strip() == "⬅️ Voltar"


def parse_money(value: str) -> float | None:
    """
    Aceita valores como:
    1500
    1500,50
    1.500,50
    R$ 1.500,50
    """
    text = (value or "").strip()
    text = text.replace("R$", "").replace(" ", "")

    if not text:
        return None

    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")

    try:
        value_decimal = Decimal(text)

        if value_decimal <= 0:
            return None

        return float(value_decimal)

    except InvalidOperation:
        return None


def parse_day(value: str) -> int | None:
    try:
        day = int((value or "").strip())

        if 1 <= day <= 31:
            return day

        return None

    except ValueError:
        return None


@router.message(F.text == "📲 Cadastro")
async def open_registration_menu(message: Message, state: FSMContext):
    await state.set_state(CadastroState.viewing_registration_menu)

    await message.answer(
        "📲 Cadastro\n\n"
        "Escolha o tipo de parâmetro que deseja cadastrar:",
        reply_markup=keyboards.cadastro_menu_keyboard()
    )


@router.message(
    StateFilter(CadastroState.viewing_registration_menu),
    F.text == "💳 Cartão de Crédito"
)
async def open_credit_card_menu(message: Message, state: FSMContext):
    await state.set_state(CadastroState.viewing_card_menu)

    await message.answer(
        "💳 Cartão de Crédito\n\n"
        "Escolha uma opção:",
        reply_markup=keyboards.cartao_menu_keyboard()
    )

@router.message(
    StateFilter(CadastroState.viewing_registration_menu),
    F.text == "⬅️ Voltar"
)
async def back_from_registration_menu(
    message: Message,
    state: FSMContext
):
    await state.clear()

    await message.answer(
        "Menu principal:",
        reply_markup=keyboards.main_menu_keyboard()
    )


@router.message(
    StateFilter(CadastroState.viewing_card_menu),
    F.text == "⬅️ Voltar"
)
async def back_from_card_menu(
    message: Message,
    state: FSMContext
):
    await state.set_state(CadastroState.viewing_registration_menu)

    await message.answer(
        "📲 Cadastro\n\n"
        "Escolha o tipo de parâmetro que deseja cadastrar:",
        reply_markup=keyboards.cadastro_menu_keyboard()
    )


@router.message(
    StateFilter(CadastroState.viewing_card_menu),
    F.text == "➕ Novo Cartão"
)
async def start_new_card(
    message: Message,
    state: FSMContext
):
    await state.set_state(CadastroState.waiting_for_card_name)

    await message.answer(
        "💳 Cadastro de cartão\n\n"
        "Digite o nome do cartão.\n"
        "Exemplo: Nubank, Itaú ou Cartão da Família.\n\n"
        "Use ⬅️ Voltar para retornar."
    )


@router.message(
    StateFilter(CadastroState.waiting_for_card_name)
)
async def receive_card_name(
    message: Message,
    state: FSMContext
):
    if is_back_command(message):
        await state.clear()

        await message.answer(
            "💳 Cartão de Crédito\n\n"
            "Escolha uma opção:",
            reply_markup=keyboards.cartao_menu_keyboard()
        )
        return

    card_name = (message.text or "").strip()

    if not card_name:
        await message.answer("❌ Digite um nome válido para o cartão.")
        return

    if len(card_name) > 100:
        await message.answer(
            "❌ O nome do cartão deve ter no máximo 100 caracteres."
        )
        return

    await state.update_data(card_name=card_name)
    await state.set_state(CadastroState.waiting_for_card_limit)

    await message.answer(
        "Digite o limite do cartão.\n"
        "Exemplo: 5000,00"
    )


@router.message(
    StateFilter(CadastroState.waiting_for_card_limit)
)
async def receive_card_limit(
    message: Message,
    state: FSMContext
):
    if is_back_command(message):
        await state.clear()

        await message.answer(
            "💳 Cartão de Crédito\n\n"
            "Escolha uma opção:",
            reply_markup=keyboards.cartao_menu_keyboard()
        )
        return

    limit = parse_money(message.text or "")

    if limit is None:
        await message.answer(
            "❌ Limite inválido.\n"
            "Digite um valor maior que zero.\n"
            "Exemplo: 5000,00"
        )
        return

    await state.update_data(card_limit=limit)
    await state.set_state(CadastroState.waiting_for_card_closing_day)

    await message.answer(
        "Digite o dia de fechamento da fatura.\n"
        "Informe um número entre 1 e 31."
    )


@router.message(
    StateFilter(CadastroState.waiting_for_card_closing_day)
)
async def receive_card_closing_day(
    message: Message,
    state: FSMContext
):
    if is_back_command(message):
        await state.clear()

        await message.answer(
            "💳 Cartão de Crédito\n\n"
            "Escolha uma opção:",
            reply_markup=keyboards.cartao_menu_keyboard()
        )
        return

    closing_day = parse_day(message.text or "")

    if closing_day is None:
        await message.answer(
            "❌ Dia de fechamento inválido.\n"
            "Informe um número entre 1 e 31."
        )
        return

    await state.update_data(card_closing_day=closing_day)
    await state.set_state(CadastroState.waiting_for_card_due_day)

    await message.answer(
        "Digite o dia de vencimento da fatura.\n"
        "Informe um número entre 1 e 31."
    )


@router.message(
    StateFilter(CadastroState.waiting_for_card_due_day)
)
async def receive_card_due_day(
    message: Message,
    state: FSMContext
):
    if is_back_command(message):
        await state.clear()

        await message.answer(
            "💳 Cartão de Crédito\n\n"
            "Escolha uma opção:",
            reply_markup=keyboards.cartao_menu_keyboard()
        )
        return

    due_day = parse_day(message.text or "")

    if due_day is None:
        await message.answer(
            "❌ Dia de vencimento inválido.\n"
            "Informe um número entre 1 e 31."
        )
        return

    data = await state.get_data()

    try:
        card = await database.criar_cartao(
            nome=data["card_name"],
            limite=data["card_limit"],
            dia_fechamento=data["card_closing_day"],
            dia_vencimento=due_day
        )

        if not card:
            raise RuntimeError("O cartão não foi retornado após o cadastro.")

        await state.clear()

        limite_formatado = f"{float(card['limite']):,.2f}"
        limite_formatado = (
            limite_formatado
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        await message.answer(
            "✅ Cartão cadastrado com sucesso!\n\n"
            f"💳 Nome: {card['nome']}\n"
            f"💰 Limite: R$ {limite_formatado}\n"
            f"📅 Fechamento: dia {card['dia_fechamento']}\n"
            f"🗓️ Vencimento: dia {card['dia_vencimento']}",
            reply_markup=keyboards.cartao_menu_keyboard()
        )

    except Exception:
        logger.exception("Erro ao cadastrar cartão")

        await state.clear()

        await message.answer(
            "❌ Não foi possível cadastrar o cartão.\n"
            "Tente novamente.",
            reply_markup=keyboards.cartao_menu_keyboard()
        )


@router.message(
    StateFilter(CadastroState.viewing_card_menu),
    F.text == "📋 Ver Cartões"
)
async def list_credit_cards(
    message: Message,
    state: FSMContext
):

    try:
        cards = await database.listar_cartoes_ativos()

        if not cards:
            await message.answer(
                "📋 Nenhum cartão de crédito cadastrado.",
                reply_markup=keyboards.cartao_menu_keyboard()
            )
            return

        lines = ["📋 Cartões cadastrados\n"]

        for card in cards:
            limite_formatado = f"{float(card['limite']):,.2f}"
            limite_formatado = (
                limite_formatado
                .replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            lines.append(
                f"💳 {card['nome']}\n"
                f"   💰 Limite: R$ {limite_formatado}\n"
                f"   📅 Fechamento: dia {card['dia_fechamento']}\n"
                f"   🗓️ Vencimento: dia {card['dia_vencimento']}\n"
            )

        await message.answer(
            "\n".join(lines),
            reply_markup=keyboards.cartao_menu_keyboard()
        )

    except Exception:
        logger.exception("Erro ao listar cartões")

        await message.answer(
            "❌ Não foi possível consultar os cartões cadastrados.",
            reply_markup=keyboards.cartao_menu_keyboard()
        )