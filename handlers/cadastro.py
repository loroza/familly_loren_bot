# handlers/cadastro.py

import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message, Document

from ofxparse import OfxParser

import database
import keyboards
import re
from datetime import datetime

logger = logging.getLogger(__name__)
router = Router()


class CadastroState(StatesGroup):
    viewing_registration_menu = State()
    viewing_card_menu = State()

    waiting_for_card_name = State()
    waiting_for_card_limit = State()
    waiting_for_card_closing_day = State()
    waiting_for_card_due_day = State()

    waiting_for_ofx_file = State()
    categorizando_ofx_categoria = State()
    categorizando_ofx_subcategoria = State()
    categorizando_ofx_pagamento = State()


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


def parse_ofx_transacoes(conteudo: str) -> list[dict]:
    """
    Parser simples e resistente para OFX 1.0 (SGML), tolerante a tags vazias
    (ex: <NAME></NAME>), que quebram bibliotecas como ofxparse.
    """
    blocos = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", conteudo, re.DOTALL | re.IGNORECASE)
    transacoes = []

    def extrair_tag(bloco: str, tag: str) -> str:
        m = re.search(rf"<{tag}>(.*?)(?:</{tag}>|\n|$)", bloco, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    for bloco in blocos:
        dtposted = extrair_tag(bloco, "DTPOSTED")
        trnamt = extrair_tag(bloco, "TRNAMT")
        memo = extrair_tag(bloco, "MEMO")
        name = extrair_tag(bloco, "NAME")
        fitid = extrair_tag(bloco, "FITID")
        trntype = extrair_tag(bloco, "TRNTYPE")

        if not dtposted or not trnamt:
            continue

        try:
            data_transacao = datetime.strptime(dtposted[:8], "%Y%m%d").date()
            valor = float(trnamt)
        except (ValueError, TypeError):
            continue

        descricao = memo or name or "Sem descrição"

        transacoes.append({
            "fitid": fitid,
            "tipo_ofx": trntype,
            "valor": valor,
            "data_transacao": data_transacao,
            "descricao": descricao,
        })

    return transacoes


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
        codigo_casa = await database.get_user_casa(message.from_user.id)
        card = await database.criar_cartao(
            nome=data["card_name"],
            limite=data["card_limit"],
            dia_fechamento=data["card_closing_day"],
            dia_vencimento=due_day,
            codigo_casa=codigo_casa
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
        codigo_casa = await database.get_user_casa(message.from_user.id)
        cards = await database.get_cartoes_by_casa(codigo_casa)

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
            reply_markup=keyboards.kb_editar_cartao_botao()
        )

    except Exception:
        logger.exception("Erro ao listar cartões")

        await message.answer(
            "❌ Não foi possível consultar os cartões cadastrados.",
            reply_markup=keyboards.cartao_menu_keyboard()
        )


class EditCartaoStates(StatesGroup):
    aguardando_valor = State()

@router.callback_query(F.data == "editar_cartao")
async def listar_cartoes_para_editar(callback: CallbackQuery, state: FSMContext):
    codigo_casa = await database.get_user_casa(callback.from_user.id)
    await state.update_data(codigo_casa=codigo_casa)
    cartoes = await database.get_cartoes_by_casa(codigo_casa)

    if not cartoes:
        await callback.message.edit_text("Você ainda não tem cartões cadastrados.")
        return

    await callback.message.edit_text(
        "Selecione o cartão que deseja editar:",
        reply_markup=keyboards.kb_lista_cartoes_editar(cartoes)
    )

@router.callback_query(F.data.startswith("editcard_"))
async def escolher_campo(callback: CallbackQuery):
    cartao_id = int(callback.data.split("_")[1])
    await callback.message.edit_text(
        "O que deseja alterar neste cartão?",
        reply_markup=keyboards.kb_campo_editar(cartao_id)
    )

@router.callback_query(F.data.startswith("editfield_"))
async def solicitar_novo_valor(callback: CallbackQuery, state: FSMContext):
    _, campo, cartao_id = callback.data.split("_")
    await state.update_data(campo_edicao=campo, cartao_id=int(cartao_id))
    await state.set_state(EditCartaoStates.aguardando_valor)

    mensagens = {
        "limite": "Digite o novo limite (ex: 5000.00):",
        "fechamento": "Digite o novo dia de fechamento da fatura (1 a 31):",
        "vencimento": "Digite o novo dia de vencimento da fatura (1 a 31):"
    }
    await callback.message.edit_text(mensagens[campo])

@router.message(StateFilter(EditCartaoStates.aguardando_valor))
async def salvar_novo_valor(message: Message, state: FSMContext):
    data = await state.get_data()
    campo = data["campo_edicao"]
    cartao_id = data["cartao_id"]
    codigo_casa = data.get("codigo_casa")

    try:
        if campo == "limite":
            novo_valor = float(message.text.replace(",", "."))
            await database.update_limite_cartao(cartao_id, codigo_casa, novo_valor)
            texto_confirmacao = f"Limite atualizado para R$ {novo_valor:.2f}."

        elif campo in ("fechamento", "vencimento"):
            novo_dia = int(message.text)
            if not (1 <= novo_dia <= 31):
                await message.answer("Digite um dia válido entre 1 e 31.")
                return
            if campo == "fechamento":
                await database.update_dia_fechamento(cartao_id, codigo_casa, novo_dia)
            else:
                await database.update_dia_vencimento(cartao_id, codigo_casa, novo_dia)
            texto_confirmacao = f"Dia de {campo} atualizado para {novo_dia}."

    except ValueError:
        await message.answer("Valor inválido. Tente novamente.")
        return

    await state.clear()
    await message.answer(f"✅ {texto_confirmacao}")


@router.message(
    StateFilter(CadastroState.viewing_registration_menu),
    F.text == "📥 Importar Extrato (.ofx)"
)
async def start_ofx_import(message: Message, state: FSMContext):
    await state.set_state(CadastroState.waiting_for_ofx_file)
    await message.answer(
        "📥 Envie o arquivo .ofx exportado pelo seu banco.\n\n"
        "Use ⬅️ Voltar para cancelar."
    )


@router.message(StateFilter(CadastroState.waiting_for_ofx_file), F.text == "⬅️ Voltar")
async def cancel_ofx_import(message: Message, state: FSMContext):
    await state.set_state(CadastroState.viewing_registration_menu)
    await message.answer(
        "📲 Cadastro\n\nEscolha o tipo de parâmetro que deseja cadastrar:",
        reply_markup=keyboards.cadastro_menu_keyboard()
    )


@router.message(StateFilter(CadastroState.waiting_for_ofx_file), F.document)
async def processar_arquivo_ofx(message: Message, state: FSMContext):
    document: Document = message.document

    if not document.file_name.lower().endswith(".ofx"):
        await message.answer("❌ Envie um arquivo com extensão .ofx")
        return

    file = await message.bot.get_file(document.file_id)
    file_path = f"/tmp/{document.file_unique_id}.ofx"
    await message.bot.download_file(file.file_path, destination=file_path)

    try:
        with open(file_path, "r", encoding="latin-1", errors="ignore") as f:
            conteudo = f.read()
        transacoes = parse_ofx_transacoes(conteudo)
    except Exception:
        logger.exception("Erro ao interpretar OFX")
        await message.answer("❌ Não consegui ler esse arquivo. Verifique se é um .ofx válido.")
        return

    if not transacoes:
        await message.answer("❌ Nenhuma transação encontrada nesse arquivo.")
        return

    codigo_casa = await database.get_user_casa(message.from_user.id)

    fila_pendentes = []
    importadas = 0

    for t in transacoes:
        descricao = t["descricao"]
        valor = t["valor"]
        tipo = "receita" if valor > 0 else "despesa"
        data_transacao = t["data_transacao"]

        conhecida = await database.buscar_categoria_aprendida(codigo_casa, descricao)
        duplicata = await database.verificar_possivel_duplicata(codigo_casa, abs(valor), data_transacao)

        payload = {
            "telegram_user_id": message.from_user.id,
            "tipo": tipo,
            "descricao": descricao,
            "valor": abs(valor),
            "data_transacao": data_transacao,
            "data_vencimento": data_transacao,
            "escopo": "pessoal",
            "tipo_pagamento": "avista",
            "status": "realizado",
            "data_pagamento": data_transacao,
            "codigo_casa": codigo_casa,
            "duplicata_info": duplicata,
        }

        if conhecida and not duplicata:
            payload["categoria_text"] = conhecida["categoria_text"]
            payload["subcategoria_text"] = conhecida["subcategoria_text"]
            payload["forma_pagamento"] = conhecida["forma_pagamento"]
            await database.insert_transacao(payload)
            importadas += 1
        else:
            if conhecida:
                payload["categoria_text"] = conhecida["categoria_text"]
                payload["subcategoria_text"] = conhecida["subcategoria_text"]
                payload["forma_pagamento"] = conhecida["forma_pagamento"]
            fila_pendentes.append(payload)

    await state.update_data(
        fila_pendentes=fila_pendentes,
        codigo_casa=codigo_casa,
        importadas=importadas
    )

    await message.answer(
        f"✅ {importadas} transações importadas automaticamente.\n"
        f"⚠️ {len(fila_pendentes)} precisam de revisão (categoria nova ou possível duplicata)."
    )

    await continuar_categorizacao_ofx(message, state)


async def continuar_categorizacao_ofx(message: Message, state: FSMContext):
    data = await state.get_data()
    fila = data.get("fila_pendentes", [])

    if not fila:
        await state.set_state(CadastroState.viewing_registration_menu)
        await message.answer(
            "🎉 Importação concluída!",
            reply_markup=keyboards.cadastro_menu_keyboard()
        )
        return

    atual = fila[0]
    await state.set_state(CadastroState.categorizando_ofx_categoria)

    duplicata = atual.get("duplicata_info")
    aviso_duplicata = ""
    if duplicata:
        aviso_duplicata = (
            f"\n⚠️ Possível duplicata!\n"
            f"Já existe lançamento de R$ {float(duplicata['valor']):.2f} "
            f"em {duplicata['data_transacao']} ({duplicata['descricao']}, "
            f"categoria: {duplicata['categoria_text']}).\n"
            f"Se for a mesma despesa/receita, toque em ⏭️ Pular esta transação.\n"
        )

    tipo_categoria = "despesas" if atual["tipo"] == "despesa" else "receitas"

    await message.answer(
        f"Revise esta transação:\n\n"
        f"📝 {atual['descricao']}\n"
        f"💰 R$ {atual['valor']:.2f}\n"
        f"📅 {atual['data_transacao']}"
        f"{aviso_duplicata}\n\n"
        f"Escolha a categoria ou pule:",
        reply_markup=keyboards.get_main_category_keyboard_com_pular(tipo_categoria)
    )


@router.message(StateFilter(CadastroState.categorizando_ofx_categoria))
async def receber_categoria_ofx(message: Message, state: FSMContext):
    data = await state.get_data()
    fila = data.get("fila_pendentes", [])

    if not fila:
        await state.set_state(CadastroState.viewing_registration_menu)
        return

    texto = (message.text or "").strip()

    if texto == "⏭️ Pular esta transação":
        fila.pop(0)
        await state.update_data(fila_pendentes=fila)
        await message.answer("⏭️ Transação ignorada (não cadastrada).")
        await continuar_categorizacao_ofx(message, state)
        return

    if texto == "⬅️ Voltar":
        await state.set_state(CadastroState.viewing_registration_menu)
        await message.answer(
            "📲 Cadastro\n\nEscolha o tipo de parâmetro que deseja cadastrar:",
            reply_markup=keyboards.cadastro_menu_keyboard()
        )
        return

    atual = fila[0]
    atual["categoria_text"] = texto
    tipo_categoria = "despesas" if atual["tipo"] == "despesa" else "receitas"

    await state.update_data(fila_pendentes=fila)
    await state.set_state(CadastroState.categorizando_ofx_subcategoria)

    await message.answer(
        "Escolha a subcategoria:",
        reply_markup=keyboards.get_subcategory_keyboard(tipo_categoria, texto)
    )


@router.message(StateFilter(CadastroState.categorizando_ofx_subcategoria))
async def receber_subcategoria_ofx(message: Message, state: FSMContext):
    data = await state.get_data()
    fila = data.get("fila_pendentes", [])

    if not fila:
        await state.set_state(CadastroState.viewing_registration_menu)
        return

    texto = (message.text or "").strip()
    atual = fila[0]
    tipo_categoria = "despesas" if atual["tipo"] == "despesa" else "receitas"

    if texto == "⬅️ Voltar":
        await state.set_state(CadastroState.categorizando_ofx_categoria)
        await message.answer(
            "Escolha a categoria ou pule:",
            reply_markup=keyboards.get_main_category_keyboard_com_pular(tipo_categoria)
        )
        return

    atual["subcategoria_text"] = texto

    await state.update_data(fila_pendentes=fila)
    await state.set_state(CadastroState.categorizando_ofx_pagamento)

    await message.answer(
        "Forma de pagamento:",
        reply_markup=keyboards.payment_method_keyboard()
    )


@router.message(StateFilter(CadastroState.categorizando_ofx_pagamento))
async def receber_pagamento_ofx(message: Message, state: FSMContext):
    data = await state.get_data()
    fila = data.get("fila_pendentes", [])
    codigo_casa = data.get("codigo_casa")

    if not fila:
        await state.set_state(CadastroState.viewing_registration_menu)
        return

    texto = (message.text or "").strip()
    atual = fila[0]
    tipo_categoria = "despesas" if atual["tipo"] == "despesa" else "receitas"

    if texto == "⬅️ Voltar":
        await state.set_state(CadastroState.categorizando_ofx_subcategoria)
        await message.answer(
            "Escolha a subcategoria:",
            reply_markup=keyboards.get_subcategory_keyboard(tipo_categoria, atual["categoria_text"])
        )
        return

    valid_options = [
        "💳 Cartão de Crédito",
        "💳 Cartão de Débito",
        "💸 Pix / Dinheiro",
        "📄 Boleto",
        "🔄 Débito Automático",
    ]
    if texto not in valid_options:
        await message.answer("❌ Escolha uma opção do teclado para a forma de pagamento.")
        return

    atual = fila.pop(0)
    atual.pop("duplicata_info", None)
    atual["forma_pagamento"] = texto

    await database.insert_transacao(atual)
    await database.salvar_categoria_aprendida(
        codigo_casa,
        atual["descricao"],
        atual["categoria_text"],
        atual["subcategoria_text"],
        atual["forma_pagamento"]
    )

    await state.update_data(fila_pendentes=fila)
    await message.answer("✅ Transação categorizada com sucesso!")
    await continuar_categorizacao_ofx(message, state)