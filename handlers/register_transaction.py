# handlers/register_transaction.py
import logging
from datetime import date, datetime
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from utils.loader import load_categories
import database
import keyboards

logger = logging.getLogger(__name__)
router = Router()

# ─── Estados do fluxo ──────────────────────────────────────────────────────────

class TransactionState(StatesGroup):
    choosing_type        = State()
    entering_value       = State()
    entering_description = State()
    choosing_category    = State()
    choosing_subcategory = State()
    choosing_scope       = State()
    choosing_pay_method  = State()
    choosing_pay_type    = State()
    entering_installments= State()
    entering_due_date    = State()
    entering_tx_date     = State()
    confirming           = State()

# ─── Helpers ───────────────────────────────────────────────────────────────────

TIPO_MAP = {
    "📈 receita": "receita",
    "📉 despesa": "despesa",
}

SCOPE_MAP = {
    "👤 pessoal": "pessoal",
    "🏠 ambos":   "ambos",
}

PAY_METHOD_MAP = {
    "💵 dinheiro":      "dinheiro",
    "💳 cartão":        "cartao",
    "🔁 pix/transfer":  "pix",
    "🏦 transferência": "transferencia",
    "🧾 outro":         "outro",
}

PAY_TYPE_MAP = {
    "💳 único":      "unico",
    "🔢 parcelado":  "parcelado",
    "🔁 recorrente": "recorrente",
}

def _parse_date(text: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None

def _summary(data: dict) -> str:
    tipo       = data.get("tipo", "-")
    valor      = data.get("valor", 0)
    desc       = data.get("descricao") or "—"
    cat        = data.get("categoria_text") or "—"
    subcat     = data.get("subcategoria_text") or "—"
    escopo     = data.get("escopo", "-")
    pay_method = data.get("forma_pagamento", "-")
    pay_type   = data.get("tipo_pagamento", "-")
    parcelas   = data.get("parcelas_total", "—")
    dt_tx      = data.get("data_transacao") or "—"
    dt_venc    = data.get("data_vencimento") or "—"

    parcela_linha = ""
    if pay_type == "parcelado":
        parcela_linha = f"\n💳 Parcelas: {parcelas}x — 1º venc.: {dt_venc}"

    return (
        f"📋 *Resumo da transação*\n\n"
        f"Tipo: {tipo.title()}\n"
        f"Valor: R$ {float(valor):.2f}\n"
        f"Descrição: {desc}\n"
        f"Categoria: {cat} › {subcat}\n"
        f"Escopo: {escopo}\n"
        f"Pagamento: {pay_method} — {pay_type}"
        f"{parcela_linha}\n"
        f"Data da compra: {dt_tx}\n\n"
        f"Confirma?"
    )

# ─── Início do fluxo ───────────────────────────────────────────────────────────

@router.message(F.text.in_(["➕ Nova Receita", "➖ Nova Despesa"]))
async def start_transaction(message: Message, state: FSMContext):
    await state.clear()
    tipo_pre = "receita" if "Receita" in message.text else "despesa"
    await state.update_data(tipo=tipo_pre)
    await state.set_state(TransactionState.entering_value)
    await message.answer(
        f"{'📈 Nova Receita' if tipo_pre == 'receita' else '📉 Nova Despesa'}\n\n"
        f"Digite o valor (ex: 150,00):",
        reply_markup=keyboards.skip_keyboard()
    )

# ─── Valor ─────────────────────────────────────────────────────────────────────

@router.message(TransactionState.entering_value)
async def enter_value(message: Message, state: FSMContext):
    try:
        valor = float(message.text.replace("R$", "").replace(".", "").replace(",", ".").strip())
        if valor <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Valor inválido. Digite novamente (ex: 150,00):")
        return

    await state.update_data(valor=valor)
    await state.set_state(TransactionState.entering_description)
    await message.answer(
        "📝 Digite uma descrição (ou pule):",
        reply_markup=keyboards.skip_keyboard()
    )

# ─── Descrição ─────────────────────────────────────────────────────────────────

@router.message(TransactionState.entering_description)
async def enter_description(message: Message, state: FSMContext):
    desc = None if message.text == "⏭ Pular" else message.text
    data = await state.get_data()
    await state.update_data(descricao=desc)
    await state.set_state(TransactionState.choosing_category)
    await message.answer(
        "🗂 Escolha a categoria:",
        reply_markup=keyboards.get_main_category_keyboard(data["tipo"])
    )

# ─── Categoria ─────────────────────────────────────────────────────────────────

@router.message(TransactionState.choosing_category)
async def choose_category(message: Message, state: FSMContext):
    if message.text == "❌ Cancelar":
        await _cancel(message, state)
        return

    data = await state.get_data()
    tipo = data["tipo"]
    categories = load_categories().get(tipo, {}).get("categorias", {})

    # Encontra a chave correspondente ao texto do botão
    chosen_key = None
    for key, value in categories.items():
        emoji = _get_emoji(value)
        label = f"{emoji} {key.replace('_', ' ').title()}" if emoji else key.replace('_', ' ').title()
        if message.text.strip() == label.strip():
            chosen_key = key
            break

    if not chosen_key:
        await message.answer("❌ Categoria inválida. Escolha uma opção do teclado:")
        return

    await state.update_data(categoria_key=chosen_key, categoria_text=chosen_key.replace("_", " ").title())
    await state.set_state(TransactionState.choosing_subcategory)
    await message.answer(
        "🔹 Escolha a subcategoria:",
        reply_markup=keyboards.get_subcategory_keyboard(tipo, chosen_key)
    )

# ─── Subcategoria ──────────────────────────────────────────────────────────────

@router.message(TransactionState.choosing_subcategory)
async def choose_subcategory(message: Message, state: FSMContext):
    if message.text == "❌ Cancelar":
        await _cancel(message, state)
        return

    if message.text == "⬅️ Voltar":
        data = await state.get_data()
        await state.set_state(TransactionState.choosing_category)
        await message.answer(
            "🗂 Escolha a categoria:",
            reply_markup=keyboards.get_main_category_keyboard(data["tipo"])
        )
        return

    subcat = message.text.replace("🔹 ", "").strip()
    await state.update_data(subcategoria_text=subcat)
    await state.set_state(TransactionState.choosing_scope)
    await message.answer(
        "👥 Essa transação é:",
        reply_markup=keyboards.scope_keyboard()
    )

# ─── Escopo ────────────────────────────────────────────────────────────────────

@router.message(TransactionState.choosing_scope)
async def choose_scope(message: Message, state: FSMContext):
    escopo = SCOPE_MAP.get(message.text.lower())
    if not escopo:
        await message.answer("❌ Escolha uma opção válida:")
        return

    await state.update_data(escopo=escopo)
    await state.set_state(TransactionState.choosing_pay_method)
    await message.answer(
        "💳 Forma de pagamento:",
        reply_markup=keyboards.payment_method_keyboard()
    )

# ─── Forma de pagamento ────────────────────────────────────────────────────────

@router.message(TransactionState.choosing_pay_method)
async def choose_pay_method(message: Message, state: FSMContext):
    pay_method = PAY_METHOD_MAP.get(message.text.lower())
    if not pay_method:
        await message.answer("❌ Escolha uma opção válida:")
        return

    await state.update_data(forma_pagamento=pay_method)
    await state.set_state(TransactionState.choosing_pay_type)
    await message.answer(
        "🔢 Tipo de pagamento:",
        reply_markup=keyboards.payment_type_keyboard()
    )

# ─── Tipo de pagamento ─────────────────────────────────────────────────────────

@router.message(TransactionState.choosing_pay_type)
async def choose_pay_type(message: Message, state: FSMContext):
    pay_type = PAY_TYPE_MAP.get(message.text.lower())
    if not pay_type:
        await message.answer("❌ Escolha uma opção válida:")
        return

    await state.update_data(tipo_pagamento=pay_type)

    if pay_type == "parcelado":
        await state.set_state(TransactionState.entering_installments)
        await message.answer("🔢 Quantas parcelas?")
    else:
        await state.update_data(parcelas_total=1)
        await state.set_state(TransactionState.entering_due_date)
        await message.answer(
            "📅 Data de vencimento (dd/mm/aaaa) ou pule:",
            reply_markup=keyboards.skip_keyboard()
        )

# ─── Parcelas ──────────────────────────────────────────────────────────────────

@router.message(TransactionState.entering_installments)
async def enter_installments(message: Message, state: FSMContext):
    try:
        parcelas = int(message.text.strip())
        if parcelas < 2:
            raise ValueError
    except ValueError:
        await message.answer("❌ Digite um número válido de parcelas (mínimo 2):")
        return

    await state.update_data(parcelas_total=parcelas)
    await state.set_state(TransactionState.entering_due_date)
    await message.answer("📅 Data do 1º vencimento (dd/mm/aaaa):")

# ─── Data de vencimento ────────────────────────────────────────────────────────

@router.message(TransactionState.entering_due_date)
async def enter_due_date(message: Message, state: FSMContext):
    if message.text == "⏭ Pular":
        await state.update_data(data_vencimento=None)
    else:
        d = _parse_date(message.text)
        if not d:
            await message.answer("❌ Data inválida. Use o formato dd/mm/aaaa:")
            return
        await state.update_data(data_vencimento=str(d))

    await state.set_state(TransactionState.entering_tx_date)
    await message.answer(
        "🗓 Data da compra (dd/mm/aaaa) ou pule para hoje:",
        reply_markup=keyboards.skip_keyboard()
    )

# ─── Data da compra ────────────────────────────────────────────────────────────

@router.message(TransactionState.entering_tx_date)
async def enter_tx_date(message: Message, state: FSMContext):
    if message.text == "⏭ Pular":
        tx_date = str(date.today())
    else:
        d = _parse_date(message.text)
        if not d:
            await message.answer("❌ Data inválida. Use o formato dd/mm/aaaa:")
            return
        tx_date = str(d)

    await state.update_data(data_transacao=tx_date)
    data = await state.get_data()
    await state.set_state(TransactionState.confirming)
    await message.answer(
        _summary(data),
        parse_mode="Markdown",
        reply_markup=keyboards.confirm_keyboard()
    )

# ─── Confirmação ───────────────────────────────────────────────────────────────

@router.message(TransactionState.confirming)
async def confirm_transaction(message: Message, state: FSMContext):
    if message.text == "❌ Cancelar":
        await _cancel(message, state)
        return

    if message.text != "✅ Confirmar":
        await message.answer("Escolha uma opção:", reply_markup=keyboards.confirm_keyboard())
        return

    data = await state.get_data()
    payload = {
        "telegram_user_id": str(message.from_user.id),
        "tipo":              data.get("tipo"),
        "categoria_text":    data.get("categoria_text"),
        "subcategoria_text": data.get("subcategoria_text"),
        "escopo":            data.get("escopo"),
        "descricao":         data.get("descricao"),
        "valor":             data.get("valor"),
        "forma_pagamento":   data.get("forma_pagamento"),
        "tipo_pagamento":    data.get("tipo_pagamento"),
        "parcelas_total":    data.get("parcelas_total"),
        "data_transacao":    data.get("data_transacao"),
        "data_vencimento":   data.get("data_vencimento"),
        "banco":             None,
    }

    try:
        await database.insert_transacao(payload)
        await state.clear()
        await message.answer(
            "✅ Transação registrada com sucesso!",
            reply_markup=keyboards.main_menu()
        )
    except Exception as e:
        logger.error(f"Erro ao salvar transação: {e}")
        await message.answer(
            "❌ Erro ao salvar. Tente novamente.",
            reply_markup=keyboards.main_menu()
        )
        await state.clear()

# ─── Cancelar ──────────────────────────────────────────────────────────────────

async def _cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Operação cancelada.", reply_markup=keyboards.main_menu())

# ─── Helper interno ────────────────────────────────────────────────────────────

def _get_emoji(value_obj: dict) -> str:
    icon = value_obj.get("icon", "")
    if isinstance(icon, str) and len(icon) <= 4:
        return icon
    return "📁"