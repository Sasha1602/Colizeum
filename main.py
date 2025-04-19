import logging
from aiogram import Dispatcher
from bot_instance import bot
from aiogram.fsm.storage.memory import MemoryStorage
from middlewares.ban_checker import BanCheckMiddleware
import asyncio
from bot_handlers_FIXED import router  # Импортируйте роутер
from config import TOKEN, DB_CONFIG
from aiomysql import create_pool
from database import set_db_pool, remove_expired_bookings

# Настройка логирования
logging.basicConfig(level=logging.INFO)

dp = Dispatcher(storage=MemoryStorage())

dp.message.outer_middleware(BanCheckMiddleware())
dp.callback_query.outer_middleware(BanCheckMiddleware())

# Включение роутера в диспетчер
dp.include_router(router)

async def main():
    print("Starting the bot...")
    try:
        pool = await create_pool(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            db=DB_CONFIG['db'],
            autocommit=True,
            minsize=1,  # минимальное количество соединений
            maxsize=5   # максимальное количество соединений
        )
        set_db_pool(pool)  # передаём пул в database.py
        asyncio.create_task(remove_expired_bookings())
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())