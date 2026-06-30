import json
import os
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def _load_categories():
    path = os.path.join(os.path.dirname(__file__), "categorias.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

CATEGORIES_DATA = _load_categories()

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Nova Receita"), KeyboardButton(text="➖ Nova Despesa")],
            [KeyboardButton(text="📊 Meu Relatório")]
        ],
        resize_keyboard=True
    )

def get_main_category_keyboard(tipo: str):
    cats = CATEGORIES_DATA.get(tipo, {}).get("categorias", {})
    buttons = []
    row = []
    for cat_key in cats.keys():
        label = cat_key.replace("_", " ").title()
        row.append(KeyboardButton(text=label))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(text="⬅️ Voltar")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_subcategory_keyboard(tipo: str, categoria_label: str):
    categoria_key = categoria_label.lower().replace(" ", "_")
    subcats = (
        CATEGORIES_DATA
        .get(tipo, {})
        .get("categorias", {})
        .get(categoria_key, {})
        .get("subcategorias", [])
    )
    buttons = []
    row = []
    for sub in subcats:
        row.append(KeyboardButton(text=sub.title()))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(text="⬅️ Voltar")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def scope_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Pessoal"), KeyboardButton(text="🏠 Ambos")]
        ],
        resize_keyboard=True
    )

def payment_method_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Cartão de Crédito"), KeyboardButton(text="💸 Pix / Dinheiro")],
            [KeyboardButton(text="📄 Boleto"), KeyboardButton(text="🔄 Débito Automático")]
        ],
        resize_keyboard=True
    )

def payment_type_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 À Vista"), KeyboardButton(text="🗓️ Parcelado")]
        ],
        resize_keyboard=True
    )