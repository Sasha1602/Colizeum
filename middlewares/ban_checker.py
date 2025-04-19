from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Union
from database import is_user_banned  # или откуда у тебя импорт

class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Any],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        banned = await is_user_banned(user_id)
        print(banned)
        if banned:
            if isinstance(event, CallbackQuery):
                await event.answer("🚫 Вы были забанены и не можете пользоваться ботом.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("🚫 Вы были забанены и не можете пользоваться ботом.")
            return None  # ⛔️ ОБЯЗАТЕЛЬНО: остановка цепочки обработки
        # Если не забанен — продолжаем
        return await handler(event, data)