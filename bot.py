import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN
from handlers import start, register_transaction, reports
import database

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_routers(
        start.router,
        register_transaction.router,
        reports.router
    )

    await database.init_db_pool()

    try:
        await dp.start_polling(bot)
    finally:
        await database.close_db_pool()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())