# keyboards.py
import re
import hashlib
import unicodedata
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from utils.loader import load_categories

# Mapeamento em memória para callbacks encurtados (hash -> raw)
# OBS: isso existe apenas em memória durante a execução.
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
    """Monta callback_data seguro e encurta se necessário."""
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
    """Resolve a versão 'raw' do callback_data encurtado."""
    if callback_data in CALLBACK_MAP:
        return CALLBACK_MAP[callback_data]
    parts = callback_data.split(":")
    if len(parts) >= 3 and parts[1] == "h":
        return CALLBACK_MAP.get(callback_data, callback_data)
    return callback_data

# --- Teclado Principal ---

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Nova Receita"), KeyboardButton(text="➖ Nova Despesa")],
            [KeyboardButton(text="📊 Meu Relatório")],
            [KeyboardButton(text="🗂 Categorias")]
        ],
        resize_keyboard=True
    )

# --- Teclados de Transação ---

def transaction_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Receita", callback_data="tipo:receita")],
        [InlineKeyboardButton(text="Despesa", callback_data="tipo:despesa")]
    ])

def scope_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Pessoal", callback_data="escopo:pessoal")],
        [InlineKeyboardButton(text="Ambos", callback_data="escopo:ambos")]
    ])

def payment_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Único", callback_data="paytype:unico")],
        [InlineKeyboardButton(text="Parcelado", callback_data="paytype:parcelado")],
        [InlineKeyboardButton(text="Recorrente", callback_data="paytype:recorrente")],
    ])

def payment_method_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Dinheiro", callback_data="paymethod:dinheiro"),
         InlineKeyboardButton(text="💳 Cartão", callback_data="paymethod:cartao")],
        [InlineKeyboardButton(text="🔁 Pix/Transfer", callback_data="paymethod:pix"),
         InlineKeyboardButton(text="🏦 Transferência", callback_data="paymethod:transferencia")],
        [InlineKeyboardButton(text="🧾 Outro", callback_data="paymethod:outro")]
    ])

# --- Teclados Dinâmicos (Categorias) ---

def _get_emoji_for_category(value_obj):
    icon = value_obj.get("icon", "")
    if isinstance(icon, str) and len(icon) <= 4:
        return icon
    return ""

def get_main_category_keyboard(tipo):
    data = load_categories()
    categories = data.get(tipo, {}).get("categorias", {})
    buttons = []
    for key, value in categories.items():
        emoji = _get_emoji_for_category(value)
        label_prefix = f"{emoji} " if emoji else ""
        label = f"{label_prefix}{key.replace('_', ' ').title()}"
        cb = _build_callback("cat", tipo, key)
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_subcategory_keyboard(tipo, categoria_key):
    data = load_categories()
    sub_list = data.get(tipo, {}).get("categorias", {}).get(categoria_key, {}).get("subcategorias", [])
    buttons = []
    for sub in sub_list:
        sub_label = sub if isinstance(sub, str) else sub.get("label", "")
        label = f"🔹 {sub_label}"
        cb = _build_callback("subcat", tipo, categoria_key, sub_label)
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])
    buttons.append([InlineKeyboardButton(text="⬅️ Voltar", callback_data=_build_callback("back_to_cats", tipo))])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Teclados de Relatório ---

def report_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Mês Atual")],
            [KeyboardButton(text="📆 Controle Mensal")],
            [KeyboardButton(text="⬅️ Voltar")]
        ],
        resize_keyboard=True
    )

def detail_inline_keyboard(ano: int, mes: int, user_id: str):
    """Botão inline para ver lançamentos detalhados de um mês."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔍 Ver Lançamentos",
            callback_data=f"detail:{ano}:{mes}:{user_id}"
        )
    ]])