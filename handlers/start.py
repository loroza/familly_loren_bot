# handlers/start.py
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
import database
import keyboards
from config import FAMILY_PASSWORD

logger = logging.getLogger(__name__)
router = Router()

class AuthState(StatesGroup):
    waiting_for_password = State()

@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = str(message.from_user.id)

    if await database.is_user_authorized(user_id):
        await message.answer(
            f"👋 Bem-vindo de volta, {message.from_user.first_name}!",
            reply_markup=keyboards.main_menu_keyboard() # <--- AQUI DEVE SER main_menu_keyboard
        )
    else:
        await state.set_state(AuthState.waiting_for_password)
        await message.answer("🔒 Olá! Digite a senha da família para acessar:")

@router.message(StateFilter(AuthState.waiting_for_password))
async def receive_password(message: Message, state: FSMContext):
    if message.text == FAMILY_PASSWORD:
        await database.authorize_user(
            str(message.from_user.id),
            message.from_user.full_name,
            message.from_user.username or ""
        )
        await state.clear()
        await message.answer(
            f"✅ Acesso liberado! Bem-vindo, {message.from_user.first_name}!",
            reply_markup=keyboards.main_menu_keyboard() # <--- AQUI DEVE SER main_menu_keyboard
        )
    else:
        await message.answer("❌ Senha incorreta. Tente novamente:")