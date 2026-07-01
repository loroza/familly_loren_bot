# keyboards.py
import re
import hashlib
import unicodedata
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from utils.loader import load_categories

CALLBACK_MAP = {}
MAX_CB_LEN = 64

def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))

def _sanitize_part(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    s = _strip_accents(s)
    s = s.replace(" ", "__")
    s = re.sub(r"[^0-9A-Za-z_\-.:]", "", s)
    return s.lower()

def _build_callback(action: str, *parts: str) -> str:
    action_s = _sanitize_part(action)
    parts_s = ":".join(_sanitize_part(p) for p in parts if p is not None and p != "")
    raw = f"{action_s}:{parts_s}" if parts_s else action_s
    if len(raw) <= MAX_CB_LEN:
        return raw
    h = hashlib.sha1(raw.encode()).hexdigest()[:12]
    short = f"{action_s}:h:{h}"
    CALLBACK_MAP[short] = raw
    return short

def resolve_callback(callback_data: str) -> str:
    if callback_data in CALLBACK_MAP:
        return CALLBACK_MAP[callback_data]
    parts = callback_data.split(":")
    if len(parts) >= 3 and parts[1] == "h":
        return CALLBACK_MAP.get(callback_data, callback_data)
    return callback_data

# ─── Menu Principal (ReplyKeyboard) ────────────────────────────────────────────

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Nova Receita"), KeyboardButton(text="➖ Nova Despesa")],
            [KeyboardButton(text="📊 Meu Relatório")],
            [KeyboardButton(text="🗂 Categorias")]
        ],
        resize_keyboard=True
    )

# ─── Escopo (ReplyKeyboard) ─────────────────────────────────────────────────────

def scope_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Pessoal"), KeyboardButton(text="🏠 Ambos")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ─── Forma de Pagamento (ReplyKeyboard) ────────────────────────────────────────

def payment_method_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Cartão de Crédito"), KeyboardButton(text="💳 Cartão de Débito")],
            [KeyboardButton(text="💸 Pix / Dinheiro"), KeyboardButton(text="📄 Boleto")],
            [KeyboardButton(text="🔄 Débito Automático")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ─── Tipo de Pagamento (ReplyKeyboard) ─────────────────────────────────────────

def payment_type_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 À Vista"), KeyboardButton(text="🗓️ Parcelado")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ─── Categorias Dinâmicas (ReplyKeyboard) ──────────────────────────────────────

def _get_emoji_for_category(value_obj):
    icon = value_obj.get("icon", "")
    if isinstance(icon, str) and len(icon) <= 4:
        return icon
    return ""

def get_main_category_keyboard(tipo: str):
    data = load_categories()
    categories = data.get(tipo, {}).get("categorias", {})
    buttons = []
    row = []
    for key, value in categories.items():
        emoji = _get_emoji_for_category(value)
        label = f"{emoji} {key.replace('_', ' ').title()}".strip()
        row.append(KeyboardButton(text=label))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(text="⬅️ Voltar")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

def get_subcategory_keyboard(tipo: str, categoria_key: str):
    data = load_categories()
    # Tenta encontrar a chave exata ou normalizada
    categories = data.get(tipo, {}).get("categorias", {})
    cat_data = categories.get(categoria_key, {})

    # Fallback: busca pela chave normalizada caso o texto venha com emoji/espaço
    if not cat_data:
        for key, value in categories.items():
            emoji = _get_emoji_for_category(value)
            label = f"{emoji} {key.replace('_', ' ').title()}".strip()
            if label == categoria_key.strip():
                cat_data = value
                break

    sub_list = cat_data.get("subcategorias", [])
    buttons = []
    row = []
    for sub in sub_list:
        sub_label = sub if isinstance(sub, str) else sub.get("label", "")
        row.append(KeyboardButton(text=sub_label))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(text="⬅️ Voltar")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

# ─── Relatório (ReplyKeyboard) ─────────────────────────────────────────────────

def report_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Mês Atual")],
            [KeyboardButton(text="📆 Controle Mensal")],
            [KeyboardButton(text="⬅️ Voltar")]
        ],
        resize_keyboard=True
    )

# ─── Ver Lançamentos (InlineKeyboard — único inline do projeto) ─────────────────

def detail_inline_keyboard(ano: int, mes: int, user_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔍 Ver Lançamentos",
            callback_data=f"detail:{ano}:{mes}:{user_id}"
        )
    ]])