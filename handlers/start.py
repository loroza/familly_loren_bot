# handlers/start.py
import logging
import re
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
import database
import keyboards

logger = logging.getLogger(__name__)
router = Router()


class AuthState(StatesGroup):
    waiting_for_password = State()


def validar_senha(senha: str) -> bool:
    if not (8 <= len(senha) <= 12):
        return False
    if not re.search(r"[A-Z]", senha):
        return False
    if not re.search(r"[a-z]", senha):
        return False
    if not re.search(r"\d", senha):
        return False
    if not re.search(r"[^A-Za-z0-9]", senha):
        return False
    return True


@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)

    if await database.is_user_authorized(user_id):
        await message.answer(
            f"👋 Bem-vindo de volta, {message.from_user.first_name}!",
            reply_markup=keyboards.main_menu_keyboard()
        )
    else:
        await state.set_state(AuthState.waiting_for_password)
        await message.answer(
            "🔒 Olá! Digite a senha da sua casa para acessar.\n\n"
            "🆕 Se você é o primeiro da sua casa a usar o bot, crie uma senha nova com:\n"
            "• 8 a 12 caracteres\n"
            "• 1 letra maiúscula\n"
            "• 1 letra minúscula\n"
            "• 1 número\n"
            "• 1 símbolo (ex: @ # $ ! %)\n\n"
            "👨‍👩‍👧 Se alguém da sua casa já usa o bot, digite a mesma senha que essa pessoa cadastrou."
        )


@router.message(StateFilter(AuthState.waiting_for_password))
async def receive_password(message: Message, state: FSMContext):
    senha = (message.text or "").strip()

    codigo_casa_existente = await database.get_casa_by_senha(senha)

    if codigo_casa_existente:
        codigo_casa = codigo_casa_existente
    else:
        if not validar_senha(senha):
            await message.answer(
                "❌ Senha não encontrada e inválida para criar uma nova casa.\n\n"
                "Para criar uma nova senha, ela precisa ter:\n"
                "• 8 a 12 caracteres\n"
                "• 1 letra maiúscula, 1 minúscula, 1 número e 1 símbolo\n\n"
                "Tente novamente:"
            )
            return
        codigo_casa = await database.criar_casa(senha)

    await database.authorize_user(
        str(message.from_user.id),
        message.from_user.full_name,
        message.from_user.username or "",
        codigo_casa
    )
    await state.clear()
    await message.answer(
        f"✅ Acesso liberado! Bem-vindo, {message.from_user.first_name}!",
        reply_markup=keyboards.main_menu_keyboard()
    )