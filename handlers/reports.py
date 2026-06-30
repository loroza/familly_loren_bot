import logging
from aiogram import Router, F
from aiogram.types import Message
import database
import keyboards

logger = logging.getLogger(__name__)
router = Router()

@router.message(F.text == "📊 Meu Relatório")
async def show_report(message: Message):
    user_id = str(message.from_user.id)
    try:
        s = await database.get_summary_for_user(user_id)
        text = (
            f"📊 *Seu Resumo Financeiro*\n\n"
            f"💚 Receitas: R$ {s['receitas']:.2f}\n\n"
            f"🔴 Despesas pessoais: R$ {s['despesas_pessoais']:.2f}\n"
            f"🟡 Despesas compartilhadas (total): R$ {s['despesas_compartilhadas_total']:.2f}\n"
            f"   ↳ Sua parte (50%%): R$ {s['despesas_compartilhadas_total'] * 0.5:.2f}\n\n"
            f"📌 Suas despesas reais: R$ {s['minhas_despesas_reais']:.2f}\n\n"
            f"{'🟢' if s['saldo'] >= 0 else '🔴'} Saldo estimado: R$ {s['saldo']:.2f}"
        )
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboards.main_menu_keyboard())
    except Exception:
        logger.exception("Erro ao gerar relatório")
        await message.answer("❌ Erro ao gerar o relatório. Tente novamente.")