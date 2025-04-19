from datetime import datetime, timedelta
from aiogram import F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from bot_instance import bot
from database import (
    execute_query,
    fetch_user_bookings,
    delete_booking,
    delete_all_user_bookings,
    check_availability,
    save_user_info,
    check_user_in_db,
    register_user,
    get_user_from_db,
    fetch_user_bookings_by_uid,
    delete_all_bookings_by_uid,
    delete_booking_by_id,
    get_payment_id_by_booking,
    set_payment_id,
    mark_booking_as_paid,
    fetch_paid_bookings,
    fetch_unpaid_bookings,
    get_booking_info,
    is_booking_already_saved,
    get_all_users,
    get_all_bookings,
    is_user_banned,
    ban_user,
    get_banned_users,
    unban_user,
    search_users_by_query,
    fetch_bookings_by_date
)
from utils import validate_phone, get_min_max_dates
from config import DB_CONFIG
import logging
from rules import RULES_PARTS
from payments import create_payment, check_payment_status
from config import ADMIN_ID, ADMIN_CHAT_ID
from payments import create_payment
from database import set_payment_id
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters  import StateFilter
from config import ADMINS
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery


logging.basicConfig(level=logging.INFO)

class CancelReason(StatesGroup):
    waiting_for_reason = State()

class AdminSearch(StatesGroup):
    waiting_for_query = State()

admin_cancel_requests = {}

router = Router()

user_data = {}
user_step = {}

max_computers_per_zone = {
    "standart": 18,
    "bootkemp": 10,
    "ps5": 2,
}


full_zone_names = {
    "standart": "Основной зал",
    "bootkemp": "Буткемп",
    "ps5": "PlayStation 5",
}

zone_computer_mapping = {
    "standart": range(1, 19),        # ПК 1–18
    "bootkemp": range(19, 29),   # ПК 19–28
    "ps5": [29, 30],             # ПК 29–30
}

ZONE_DURATION_PRICES = {
    "standart": {1: 120, 3: 300, 5: 490},
    "bootkemp": {1: 150, 3: 370, 5: 610},
    "ps5":      {1: 220, 3: 600, 5: 1000}
}

async def show_actions_to_user(user_id, bot):
    message = await bot.send_message(chat_id=user_id, text="Выберите желаемое действие:")
    await show_actions(message)

def validate_computers(zone, computer_numbers):
    """
    Проверяет, что номера компьютеров соответствуют выбранной зоне.
    """
    valid_numbers = zone_computer_mapping.get(zone, [])
    for num in computer_numbers:
        if int(num) not in valid_numbers:
            return False
    return True

async def show_zone_selection(call_or_message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Основной зал", callback_data="standart")],
            [InlineKeyboardButton(text="Буткемп", callback_data="bootkemp")],
            [InlineKeyboardButton(text="PS5", callback_data="ps5")],
            [InlineKeyboardButton(text="⬅ Назад в меню", callback_data="back_to_menu")]
        ]
    )
    if hasattr(call_or_message, 'message'):
        await call_or_message.message.answer("Выберите желаемую зону:", reply_markup=keyboard)
    else:
        await call_or_message.answer("Выберите желаемую зону:", reply_markup=keyboard)

async def send_week_calendar(uid, message: Message):
    """
    Отправляет inline-клавиатуру с датами на ближайшую неделю.
    """
    now = datetime.now()
    dates = [(now + timedelta(days=i)).strftime("%d.%m.%Y") for i in range(7)]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for date in dates:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text=date, callback_data=f"date:{date}")])
    
    zone = user_data[uid].get("selected_zone")
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_computers")])

    
    await message.answer("Выберите дату для бронирования:", reply_markup=keyboard)

def choosing_actions(uid):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👾 Забронировать 👾", callback_data="book")],
            [InlineKeyboardButton(text="⚡️ Мои брони ⚡️", callback_data="my_bookings")],
            [InlineKeyboardButton(text="💳 Оплатить 💳", callback_data="pay")],
            [InlineKeyboardButton(text="📜 Правила 📜", callback_data="rules")]
        ]
    )

    if uid in ADMINS:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text="🛠 Меню администратора", callback_data="admin_menu")]
        )
    return keyboard

async def safe_edit_reply_markup(message, reply_markup=None):
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise

async def show_computer_selection(uid, message: Message):
    selected_zone = user_data[uid]["selected_zone"]
    valid_numbers = zone_computer_mapping[selected_zone]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(num), callback_data=f"computer:{num}")]
        for num in valid_numbers
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_number")
    ])
    await message.answer("Выберите устройства для бронирования:", reply_markup=keyboard)

async def show_admin_menu(message: Message):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Все брони", callback_data="admin_all_bookings")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="search_user")],
        [InlineKeyboardButton(text="🚫 Заблокированные", callback_data="banned_users")],
    ])
    await message.answer("Выберите действие администратора:", reply_markup=markup)

@router.message(F.text.lower() == "/start")
async def start(message: Message):
    uid = message.from_user.id
    if await is_user_banned(uid):
        await message.answer("❌ Вы были забанены администратором.")
        return
    
    # Если администратор — сразу открыть админ-меню
    if uid in ADMINS:
        await message.answer("🔧 Добро пожаловать в админ-панель.")
        await show_admin_menu(message)
        return

    if "nikname" in user_data.get(uid, {}):
        greeting = f"Добро пожаловать обратно, {user_data[uid]['nikname']}!"
        await message.answer(greeting)
        await show_actions(message)
    else:
        greeting = "Здравствуйте! Вы уже пользовались этим ботом?"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Да", callback_data="yes")],
                [InlineKeyboardButton(text="Нет", callback_data="no")]
            ]
        )
        await message.answer(greeting, reply_markup=keyboard)
        user_data[uid] = {}
        user_step[uid] = "awaiting_account"

@router.callback_query(F.data.in_({"yes", "no"}))
async def handle_registration(call: CallbackQuery):
    uid = call.from_user.id
    logging.info(f"User {uid} selected: {call.data}")
    if call.data == "yes":
        user_exists = await check_user_in_db(uid)
        logging.info(f"User {uid} exists in DB: {user_exists}")
        if user_exists:
            await call.message.answer("Добро пожаловать обратно!")
            await show_actions(call.message)
        else:
            await call.message.answer("Вы не зарегистрированы. Пожалуйста, введите ваш номер телефона для регистрации:")
            user_step[uid] = "awaiting_new_phone"
    elif call.data == "no":
        user_exists = await check_user_in_db(uid)
        logging.info(f"User {uid} exists in DB: {user_exists}")
        if user_exists:
            await call.message.answer("Добро пожаловать обратно!")
            await show_actions(call.message)
        else:
            await call.message.answer("Введите ваш номер телефона для регистрации:")
            user_step[uid] = "awaiting_new_phone"
    await call.answer()

@router.message(F.func(lambda m: user_step.get(m.from_user.id) == "awaiting_nickname"))
async def get_nickname(message: Message):
    uid = message.from_user.id
    user_data[uid]["nikname"] = message.text.strip()
    await message.answer("Отлично! Теперь введите номер телефона:")
    user_step[uid] = "awaiting_phone"


@router.message(F.func(lambda m: user_step.get(m.from_user.id) == "awaiting_phone"))
async def get_phone(message: Message):
    uid = message.from_user.id
    phone_text = message.text.strip()
    if validate_phone(phone_text):
        user_data[uid]["telefhone"] = phone_text
        await message.answer("Номер телефона сохранён.")
        await show_actions(message)
    else:
        await message.answer("Пожалуйста, введите корректный номер телефона.")


@router.message(F.func(lambda m: user_step.get(m.from_user.id) == "awaiting_new_phone"))
async def get_new_phone(message: Message):
    uid = message.from_user.id
    phone_text = message.text.strip()
    if validate_phone(phone_text):
        user_data[uid]["telefhone"] = phone_text
        await message.answer("Номер телефона сохранён. Теперь введите ваш никнейм:")
        user_step[uid] = "awaiting_new_nickname"
    else:
        await message.answer("Пожалуйста, введите корректный номер телефона.")


@router.message(F.func(lambda m: user_step.get(m.from_user.id) == "awaiting_new_nickname"))
async def get_new_nickname(message: Message):
    uid = message.from_user.id
    nickname = message.text.strip()
    user_data[uid]["nikname"] = nickname
    phone_number = user_data[uid].get("telefhone", "")
    if not phone_number:
        await message.answer("Ошибка: номер телефона не найден. Пожалуйста, начните регистрацию заново.")
        return
    await register_user(uid, phone_number, nickname)
    await message.answer(f"Никнейм '{nickname}' успешно сохранён!")
    await show_actions(message)
    user_step[uid] = None

@router.callback_query(F.data == "book")
async def handle_book_button(call: CallbackQuery):
    uid = call.from_user.id
    await call.answer()

    user_data.setdefault(uid, {})
    user_step[uid] = "awaiting_zone"

    await safe_edit_reply_markup(call.message, reply_markup=None)
    await call.message.answer(
        "Выберите желаемую зону:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Основной зал", callback_data="standart")],
                [InlineKeyboardButton(text="Буткемп", callback_data="bootkemp")],
                [InlineKeyboardButton(text="PS5", callback_data="ps5")],
                [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_menu")]
            ]
        )
    )

@router.callback_query(F.data.in_(["standart", "bootkemp", "ps5"]))
async def handle_zone_selection(call: CallbackQuery):
    uid = call.from_user.id
    if user_step.get(uid) != "awaiting_zone":
        await call.answer("Зона уже выбрана", show_alert=True)
        return

    await process_zone_selection(uid, call.data, call.message)
    await call.answer()

@router.message(F.func(lambda m: user_step.get(m.from_user.id) == "awaiting_number_of_computers"))
async def ask_for_computer_numbers(message: Message):
    uid = message.from_user.id
    input_text = message.text.strip()

    if not input_text.isdigit():
        await message.answer("Пожалуйста, введите целое число.")
        return

    count = int(input_text)
    selected_zone = user_data[uid]["selected_zone"]
    max_computers = max_computers_per_zone[selected_zone]

    if count <= 0 or count > max_computers:
        await message.answer(
            f"Число должно быть положительным и не больше {max_computers} для зоны {selected_zone}."
        )
        return

    user_data[uid]["number_of_computers"] = count
    user_data[uid]["selected_computers"] = []
    user_step[uid] = "awaiting_computer_selection"

    await show_computer_selection(uid, message)

@router.callback_query(F.data.startswith("computer:"))
async def handle_computer_selection(call: CallbackQuery):
    uid = call.from_user.id
    if user_step.get(uid) != "awaiting_computer_selection":
        await call.answer("Этап уже завершён", show_alert=True)
        return

    computer_number = int(call.data.split(":")[1])

    user_data.setdefault(uid, {})
    user_data[uid].setdefault("selected_computers", [])

    if computer_number in user_data[uid]["selected_computers"]:
        await call.answer("⚠️ Компьютер уже выбран", show_alert=True)
        return

    user_data[uid]["selected_computers"].append(computer_number)
    await call.answer(f"✅ Компьютер {computer_number} выбран.")

    # Если выбрано нужное количество ПК — показываем ✅ и убираем остальные
    if len(user_data[uid]["selected_computers"]) >= user_data[uid].get("number_of_computers", 1):
        selected_comps = user_data[uid]["selected_computers"]
        label = ", ".join([f"ПК {num}" for num in selected_comps])

        # Отображаем только выбранные ПК с галочкой
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ ПК {num}", callback_data="none")]
                for num in selected_comps
            ]
        )
        await call.message.edit_reply_markup(reply_markup=keyboard)

        await call.message.answer("Устройства успешно выбраны. Пожалуйста, выберите дату бронирования.")
        await send_week_calendar(uid, call.message)
        user_step[uid] = "awaiting_date"

@router.callback_query(F.data.startswith("date:"))
async def handle_date_selection(call: CallbackQuery):
    uid = call.from_user.id
    if user_step.get(uid) != "awaiting_date":
        await call.answer("Этап уже пройден", show_alert=True)
        return

    selected_date = call.data.split(":")[1]
    user_data[uid]["booking_date"] = selected_date
    user_step[uid] = "awaiting_time"

    # ✅ Отображаем клавиатуру только с выбранной датой с галочкой
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ {selected_date}", callback_data="none")]
        ]
    )
    await call.message.edit_reply_markup(reply_markup=keyboard)

    # 📤 Показываем выбор времени
    await send_time_selection(uid, selected_date, call.message)
    await call.answer()

@router.callback_query(F.data.startswith("time:"))
async def handle_time_selection(call: CallbackQuery):
    uid = call.from_user.id
    if user_step.get(uid) != "awaiting_time":
        await call.answer("Этап уже пройден", show_alert=True)
        return

    parts = call.data.split(":")
    if len(parts) < 4:
        await call.answer("Неверный формат данных.", show_alert=True)
        return

    _, date, hours, minutes = parts
    time = f"{hours}:{minutes}"

    try:
        datetime.strptime(time, "%H:%M")
    except ValueError:
        await call.answer("Некорректное время.", show_alert=True)
        return

    user_data[uid]["selected_time"] = time
    user_data[uid]["booking_date"] = date
    user_step[uid] = "confirm_booking"

    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"✅ {time}", callback_data="none")]]
    ))

    data = user_data[uid]
    zone_name = full_zone_names.get(data.get("selected_zone"), "Не выбрано")

    booking_details = (
        f"Дата: {data['booking_date']}, Время: {data['selected_time']}\n"
        f"Зона: {zone_name}"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 час", callback_data="duration:1")],
            [InlineKeyboardButton(text="3 часа", callback_data="duration:3")],
            [InlineKeyboardButton(text="5 часа", callback_data="duration:5")],
            [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_date")]
        ]
    )
    await call.message.answer("⏱ Выберите продолжительность бронирования:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("duration:"))
async def handle_duration_selection(call: CallbackQuery):
    uid = call.from_user.id
    duration = int(call.data.split(":")[1])

    user_data[uid]["duration"] = duration
    user_step[uid] = "confirm_booking"

    await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"✅ {duration} ч.", callback_data="none")]]
    ))

    # Обновляем краткое описание брони
    data = user_data[uid]
    zone_name = full_zone_names.get(data.get("selected_zone"), "Не выбрано")

    booking_details = (
        f"Дата: {data['booking_date']}, Время: {data['selected_time']}\n"
        f"Зона: {zone_name}\n"
        f"Длительность: {duration} ч."
    )

    await call.message.answer(
        booking_details,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Подтвердить бронь", callback_data="confirm_booking")],
                [InlineKeyboardButton(text="Отменить", callback_data="cancelation")]
            ]
        )
    )
    await call.answer()

@router.callback_query(F.data == "confirm_booking")
async def confirm_booking(call: CallbackQuery):
    uid = call.from_user.id
    try:
        if uid not in user_data:
            logging.error(f"User {uid} not found in user_data during booking confirmation.")
            await call.message.answer("Произошла ошибка при сохранении данных.")
            return

        data = user_data[uid]

        # Загружаем данные из БД
        user_db_data = await get_user_from_db(uid)
        data["nikname"] = user_db_data.get("nickname")
        data["telefhone"] = user_db_data.get("phone")

        required_fields = [
            "nikname", "telefhone", "selected_zone", "number_of_computers",
            "selected_time", "booking_date", "selected_computers"
        ]

        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            missing_fields_str = ", ".join(missing_fields)
            logging.warning(f"User {uid} is missing required fields: {missing_fields_str}")
            await call.message.answer(
                f"Не удалось сохранить бронирование, так как отсутствуют следующие данные: {missing_fields_str}."
            )
            return

        # Проверка доступности
        available = True
        conflicts = []
        if data["selected_zone"] not in ["ps5"]:
            computer_ids = data["selected_computers"]
            booking_date = data["booking_date"]
            booking_time = data["selected_time"]
            duration = data["duration"]
            available, conflicts = await check_availability(computer_ids, booking_date, booking_time, duration)

        if not available:
            logging.warning(f"User {uid} tried to book unavailable computers: {conflicts}")
            conflict_lines = [f"ПК №{comp} занят до {end_time}" for comp, end_time in conflicts]
            conflict_text = "\n".join(conflict_lines)

            await call.message.answer(
                f"❌ Бронь не может быть завершена.\n"
                f"Следующие компьютеры уже заняты на {data['booking_date']} с {data['selected_time']} "
                f"на {data['duration']} ч.:\n\n"
                f"{conflict_text}\n\n"
                "Попробуйте выбрать другое время или другие компьютеры."
            )

            data.pop("selected_computers", None)
            data.pop("selected_time", None)
            data.pop("booking_date", None)
            user_step[uid] = "awaiting_computer_selection"

            selected_zone = data["selected_zone"]
            valid_numbers = range(1, 19) if selected_zone == "standart" else (
                range(19, 29) if selected_zone == "bootkemp" else range(29, 31)
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=str(num), callback_data=f"computer:{num}")]
                for num in valid_numbers
            ])
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_zone")
            ])

            await call.message.answer("Пожалуйста, выберите компьютеры заново:", reply_markup=keyboard)
            return

        # Всё хорошо — отправка администратору
        logging.info(f"User {uid} successfully booked: {data}")
        booking_date = data["booking_date"]
        booking_time = data["selected_time"]
        zone = full_zone_names.get(data["selected_zone"], data["selected_zone"])
        nickname = data["nikname"]
        phone = data["telefhone"]
        computers = data["selected_computers"]
        comp_str = f"ПК: {', '.join(map(str, computers))}" if computers else "Консоль"

        admin_text = (
            f"📥 *Заявка на бронь:*\n"
            f"👤 `{nickname}` | 📱 `{phone}`\n"
            f"🗓 {booking_date} ⏰ {booking_time} на {data['duration']} ч.\n"
            f"🖥 {zone} | {comp_str}"
        )

        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Подтвердить бронь", callback_data=f"admin_confirm:{uid}"),
            InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"admin_cancel:{uid}")
        ]])

        await call.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, reply_markup=markup, parse_mode="Markdown")
        await call.message.answer("🕐 Ваша заявка на бронь отправлена администратору. Ожидайте подтверждения.")

    except Exception as e:
        logging.error(f"Error confirming booking for user {uid}: {e}")
        await call.message.answer("❌ Произошла ошибка при подтверждении бронирования. Пожалуйста, попробуйте снова.")

    await call.answer()

@router.callback_query(F.data == "cancellation")
async def handle_cancellation(call: CallbackQuery):
    uid = call.from_user.id
    phone_number = user_data[uid].get("telefhone", "")
    nickname = user_data[uid].get("nikname", "")
    bookings = await fetch_user_bookings(phone_number, nickname)
    if not bookings:
        await call.message.answer("У вас нет активных броней.")
        return
    markup = InlineKeyboardMarkup()
    for booking in bookings:
        try:
            booking_id, date, time, zone, computers = booking
            if computers is not None:
                button_text = f"{date.strftime('%d.%m.%Y')}, {time}, Зона: {zone},  Компьютеры: {computers}"
            else:
                button_text = f"{date.strftime('%d.%m.%Y')}, {time}, {zone}"
            markup.add(InlineKeyboardButton(text=button_text, callback_data=f"cancel_{booking_id}"))
        except ValueError as e:
            print(f"Ошибка обработки данных бронирования: {e}")
    markup.add(InlineKeyboardButton(text="Отменить все бронирования", callback_data="cancel_all"))
    await call.message.answer(
        "Выберите бронь для отмены или отмените все сразу:", reply_markup=markup
    )
    await call.answer()

async def show_actions(message: Message):
    keyboard = choosing_actions(message.from_user.id)

    try:
        await message.edit_text("Выберите желаемое действие:", reply_markup=keyboard)
    except Exception:
        await message.answer("Выберите желаемое действие:", reply_markup=keyboard)

@router.callback_query(F.data == "cancelation")
async def handle_cancel_booking_from_confirmation(call: CallbackQuery):
    uid = call.from_user.id

    # Удаляем временные данные пользователя
    user_data.pop(uid, None)
    user_step[uid] = None

    await safe_edit_reply_markup(call.message, reply_markup=None)
    await call.message.answer("❌ Бронирование отменено.")
    await show_actions(call.message)
    await call.answer()


@router.callback_query(F.data == "cancel_booking")
async def handle_cancel_booking(call: CallbackQuery):
    uid = call.from_user.id
    user_step[uid] = "cancelling"

    bookings = await fetch_user_bookings_by_uid(uid)

    if not bookings:
        await call.message.answer("У вас нет активных броней.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[])

    for booking in bookings:
        booking_id, booking_date, booking_time, zone, computers = booking
        button_text = f"{booking_date.strftime('%d.%m.%Y')} {booking_time} | {zone} | ПК: {computers or 'N/A'}"
        markup.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"cancel:{booking_id}")
        ])

    markup.inline_keyboard.append([
        InlineKeyboardButton(text="Отменить все бронирования", callback_data="cancel_all")
    ])

    markup.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_menu")
    ])

    
    await call.message.answer("Выберите бронь для отмены:", reply_markup=markup)

@router.callback_query(F.data.startswith("cancel:"))
async def handle_cancel_specific_booking(call: CallbackQuery):
    uid = call.from_user.id
    booking_id = call.data.split(":")[1]

    # 1. Получаем бронь ДО удаления
    booking = await execute_query(
        "SELECT booking_date, booking_time, zone, computers FROM UserInfo WHERE id = %s",
        (booking_id,),
        fetch=True
    )

    # 2. Удаляем бронь
    await delete_booking_by_id(booking_id)
    await call.answer("✅ Бронь отменена", show_alert=True)

    # 3. Обновляем клавиатуру
    markup = call.message.reply_markup
    new_keyboard = []

    for row in markup.inline_keyboard:
        new_row = [btn for btn in row if btn.callback_data != f"cancel:{booking_id}"]
        if new_row:
            new_keyboard.append(new_row)

    await call.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard)
    )

    # 4. Уведомляем админа (если бронь была найдена)
    if booking:
        booking_date, booking_time, zone, computers, duration  = booking[0]
        user = await get_user_from_db(uid)
        zone_name = full_zone_names.get(zone, zone)
        comp_str = f"ПК: {computers}" if computers else "Консоль"

        admin_text = (
            f"⚠️ *Бронь отменена пользователем:*\n"
            f"👤 `{user.get('nickname')}` | 📱 `{user.get('phone')}`\n"
            f"🗓 {booking_date} ⏰ {booking_time} на {duration} ч.\n"
            f"🖥 {zone_name} | {comp_str}\n"
        )

        try:
            await call.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"[CANCEL_NOTIFY] Ошибка при уведомлении админа: {e}")

@router.callback_query(F.data == "cancel_all")
async def handle_cancel_all_bookings(call: CallbackQuery):
    uid = call.from_user.id
    if user_step.get(uid) != "cancelling":
        await call.answer("Этап уже завершён", show_alert=True)
        return

    await delete_all_bookings_by_uid(uid)
    await call.message.answer("✅ Все ваши бронирования успешно отменены.")
    await show_actions(call.message)
    user_step[uid] = None

# Возврат в главное меню
@router.callback_query(F.data == "back_to_menu")
async def handle_back_to_menu(call: CallbackQuery):
    uid = call.from_user.id
    user_step[uid] = None
    await call.message.edit_reply_markup(reply_markup=None)
    await show_actions(call.message)
    await call.answer()

@router.callback_query(F.data == "back_to_date")
async def handle_back_to_date(call: CallbackQuery):
    uid = call.from_user.id
    user_step[uid] = "awaiting_date"
    user_data[uid].pop("selected_time", None)
    await call.message.edit_reply_markup(reply_markup=None)
    await send_week_calendar(uid, call.message)
    await call.answer()


@router.message(StateFilter(None))
async def handle_any_message(message: Message, state: FSMContext):
    uid = message.from_user.id
    current_state = await state.get_state()

    if uid in ADMINS and current_state != AdminSearch.waiting_for_query.state:
        return

    user_exists = await check_user_in_db(uid)

    if user_exists:
        await show_actions(message)
    else:
        await message.answer("Здравствуйте! Вы не зарегистрированы. Пожалуйста, используйте команду /start для регистрации.")

@router.callback_query(F.data == "back_to_number")
async def handle_back_to_number(call: CallbackQuery):
    uid = call.from_user.id
    user_step[uid] = "awaiting_number_of_computers"
    user_data[uid].pop("number_of_computers", None)
    user_data[uid].pop("selected_computers", None)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Сколько компьютеров хотите забронировать?")
    await call.answer()

# Обработка возврата к выбору компьютеров
@router.callback_query(F.data == "back_to_computers")
async def handle_back_to_computers(call: CallbackQuery):
    uid = call.from_user.id
    user_step[uid] = "awaiting_computer_selection"
    user_data[uid].pop("booking_date", None)
    user_data[uid].pop("selected_time", None)
    user_data[uid]["selected_computers"] = []

    selected_zone = user_data[uid]["selected_zone"]
    valid_numbers = zone_computer_mapping[selected_zone]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=str(num), callback_data=f"computer:{num}")]
        for num in valid_numbers
    ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_number")
    ])

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Выберите устройства для бронирования:", reply_markup=keyboard)
    await call.answer()

@router.callback_query(F.data == "back_to_zone")
async def handle_back_to_zone(call: CallbackQuery):
    uid = call.from_user.id

    # Чистим всё после выбора зоны
    for field in ["selected_zone", "number_of_computers", "selected_computers", "booking_date", "selected_time"]:
        user_data[uid].pop(field, None)

    user_step[uid] = "awaiting_zone"

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Теперь выберите зону:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Основной зал", callback_data="standart")],
                [InlineKeyboardButton(text="Буткемп", callback_data="bootkemp")],
                [InlineKeyboardButton(text="PS5", callback_data="ps5")],
                [InlineKeyboardButton(text="⬅ Назад в меню", callback_data="back_to_menu")]
            ]
        )
    )
    await call.answer()

@router.callback_query(F.data == "rules")
async def handle_rules(call: CallbackQuery):
    await call.answer()

    for i, part in enumerate(RULES_PARTS):
        if i == len(RULES_PARTS) - 1:
            await call.message.answer(part, parse_mode="HTML", reply_markup=rules_back_keyboard)
        else:
            await call.message.answer(part, parse_mode="HTML")

rules_back_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⬅ Назад в меню", callback_data="back_to_menu")]
    ]
)

async def process_zone_selection(uid: int, selected_zone: str, message: Message):
    user_data[uid]["selected_zone"] = selected_zone
    user_step[uid] = "awaiting_number_of_computers"

    selected_text = f"✅ {full_zone_names.get(selected_zone, selected_zone)}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=selected_text, callback_data="none")]
        ]
    )
    await message.edit_reply_markup(reply_markup=keyboard)

    user_step[uid] = "awaiting_number_of_computers"
    await message.answer(
        f"Сколько компьютеров хотите забронировать?\n\n"
        f"В зоне «{full_zone_names[selected_zone]}» доступно {max_computers_per_zone[selected_zone]} устройств.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_zone")]]
        )
    )

async def send_time_selection(uid: int, selected_date: str, message: Message):
    now = datetime.now()
    times_list = []
    start_hour = 0 if datetime.strptime(selected_date, "%d.%m.%Y").date() != now.date() else now.hour + 1

    for hour in range(start_hour, 24):
        for minute in [0, 30]:
            time_string = f"{hour:02}:{minute:02}"
            callback_data = f"time:{selected_date}:{time_string}"
            times_list.append(InlineKeyboardButton(text=time_string, callback_data=callback_data))

    markup = InlineKeyboardMarkup(inline_keyboard=[])

    for i in range(0, len(times_list), 6):
        markup.inline_keyboard.append(times_list[i:i + 6])

    markup.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад к выбору даты", callback_data="back_to_date")
    ])

    await message.answer(
        f"Вы выбрали дату: {selected_date}. Выберите время:",
        reply_markup=markup
    )

@router.callback_query(F.data.startswith("pay:"))
async def handle_payment_prompt(call: CallbackQuery):
    uid = call.from_user.id
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer("Ошибка данных.", show_alert=True)
        return

    booking_id, zone = parts[1], parts[2]
    duration = user_data[uid].get("duration", 1)
    price = ZONE_DURATION_PRICES.get(zone, {}).get(duration, 100)

    await call.message.answer(
        f"💳 Вы уверены, что хотите оплатить бронь на сумму {price}₽?\n"
        f"Зона: {full_zone_names.get(zone, zone.upper())}, на {duration} ч.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_pay:{booking_id}:{zone}")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="back_to_pay_selection")]
        ])
    )
    await call.answer()

@router.callback_query(F.data == "pay")
async def show_unpaid_bookings(call: CallbackQuery):
    uid = call.from_user.id
    bookings = await fetch_user_bookings_by_uid(uid)
    if not bookings:
        await call.message.answer("У вас нет активных броней для оплаты.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[])
    for booking in bookings:
        booking_id, date, time, zone, _, duration = booking

        # Получаем цену по зоне и длительности
        price = ZONE_DURATION_PRICES.get(zone, {}).get(duration, 100)

        # Метка кнопки
        label = f"{date.strftime('%d.%m.%Y')} в {time} — {zone.upper()} — {duration} ч. — {price}₽"
        markup.inline_keyboard.append([InlineKeyboardButton(
            text=label,
            callback_data=f"pay:{booking_id}:{zone}"
        )])

    markup.inline_keyboard.append([InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_menu")])
    await call.message.answer("Выберите бронь для оплаты:", reply_markup=markup)

@router.callback_query(F.data.startswith("confirm_pay:"))
async def confirm_pay(call: CallbackQuery):
    uid = call.from_user.id

    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer("Ошибка данных.", show_alert=True)
        return

    booking_id, zone = parts[1], parts[2]
    duration = user_data.get(uid, {}).get("duration", 1)
    price = ZONE_DURATION_PRICES.get(zone, {}).get(duration, 100)

    user = await get_user_from_db(call.from_user.id)
    if not user:
        await call.message.answer("Не удалось найти ваш аккаунт.")
        return

    description = f"Оплата бронирования #{booking_id} ({zone.upper()})"
    return_url = "https://t.me/Playschamp_Bot"

    try:
        payment_url, payment_yk_id = create_payment(price, description, return_url)
        await set_payment_id(booking_id, payment_yk_id)

        await call.message.answer(
            f"💸 Ссылка на оплату:\n{payment_url}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Я оплатил", callback_data=f"confirm_payment:{booking_id}")]])
        )
    except Exception as e:
        print(f"[PAY ERROR] {e}")
        await call.message.answer("❌ Не удалось создать платёж. Попробуйте позже.")
    await call.answer()

@router.callback_query(F.data == "back_to_pay_selection")
async def back_to_payment_list(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)
    await show_unpaid_bookings(call)
    await call.answer()

@router.callback_query(F.data.startswith("confirm_payment:"))
async def confirm_payment_status(call: CallbackQuery):
    from payments import check_payment_status
    uid = call.from_user.id
    booking_id = call.data.split(":")[1]

    payment_id = await get_payment_id_by_booking(booking_id)
    if not payment_id:
        await call.message.answer("⚠️ Не удалось найти информацию о платеже.")
        return

    status = check_payment_status(payment_id)
    if status == "succeeded":
        await mark_booking_as_paid(booking_id)
        await call.message.answer("✅ Оплата подтверждена! Бронь активна.")
        await show_actions(call.message)
    
        # 🔔 Уведомление админу
        user = await get_user_from_db(uid)
        booking_info = await get_booking_info(booking_id)  # Эту функцию нужно добавить
    
        if booking_info:
            booking_date, booking_time, zone, computers, duration  = booking_info
            zone_name = full_zone_names.get(zone, zone)
            comp_str = f"ПК: {computers}" if computers else "Консоль"
    
            admin_text = (
                f"💰 *Оплата подтверждена:*\n"
                f"👤 `{user.get('nickname')}` | 📱 `{user.get('phone')}`\n"
                f"🗓 {booking_date} ⏰ {booking_time} на {duration} ч.\n"
                f"🖥 {zone_name} | {comp_str}\n"
            )
    
            await call.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, parse_mode="Markdown")
    elif status == "pending":
        await call.message.answer("⌛ Оплата ещё не завершена. Попробуйте чуть позже.")
    else:
        await call.message.answer(f"❌ Статус платежа: {status}. Оплата не подтверждена.")

@router.callback_query(F.data == "my_bookings")
async def handle_my_bookings(call: CallbackQuery):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оплаченные ✅", callback_data="paid_bookings")],
        [InlineKeyboardButton(text="💸 Неоплаченные 💸", callback_data="unpaid_bookings")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_menu")]
    ])
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Выберите тип бронирований:", reply_markup=markup)

@router.callback_query(F.data == "unpaid_bookings")
async def handle_unpaid_bookings(call: CallbackQuery):
    uid = call.from_user.id
    user_step[uid] = "cancelling"

    bookings = await fetch_unpaid_bookings(uid)

    if not bookings:
        await call.message.answer("У вас нет неоплаченных броней.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[])

    for booking in bookings:
        booking_id, date, time, zone, computers = booking

        # Форматируем значения
        date_str = date.strftime('%d.%m') if hasattr(date, 'strftime') else str(date)[:5]
        if hasattr(time, 'strftime'):
            time_str = time.strftime('%H:%M')
        else:
            parts = str(time).split(":")
            time_str = f"{parts[0]}:{parts[1]}" if len(parts) >= 2 else str(time)
        zone_name = full_zone_names.get(zone, zone)  # ← полное название зоны
        comp_str = str(computers) if computers else ""

        # Собираем текст кнопки
        button_text = f"{date_str} | {time_str} | {zone_name} | {comp_str}".strip(" |")

        markup.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"cancel:{booking_id}")
        ])

    markup.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад в меню", callback_data="back_to_menu")
    ])

    await call.message.answer("🧾 Ваши неоплаченные брони:", reply_markup=markup)

@router.callback_query(F.data == "paid_bookings")
async def handle_my_bookings(call: CallbackQuery):
    uid = call.from_user.id
    bookings = await fetch_paid_bookings(uid)

    if not bookings:
        await call.message.answer("У вас нет оплаченных броней.")
        return

    text = "🧾 Ваши оплаченные брони:\n\n"
    for booking in bookings:
        date, time, zone, computers = booking

        # Обработка значений
        parts = []

        # Дата
        if date:
            date_str = date.strftime('%d.%m.%Y') if hasattr(date, 'strftime') else str(date)
            parts.append(date_str)

        # Время
        if time:
            time_str = time.strftime('%H:%M') if hasattr(time, 'strftime') else str(time)[:5]
            parts.append(time_str)

        # Зона
        if zone:
            zone_name = full_zone_names.get(zone, zone)
            parts.append(zone_name)

        # Компьютеры
        if computers:
            parts.append(str(computers))

        text += "• " + " | ".join(parts) + "\n"

    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅ Назад в меню", callback_data="back_to_menu")]]
    )
    await call.message.answer(text, reply_markup=markup)

@router.callback_query(F.data.startswith("admin_confirm:"))
async def admin_confirm_booking(call: CallbackQuery):
    if call.from_user.id not in ADMINS:
        await call.answer("⛔ У вас нет прав для выполнения этой операции.", show_alert=True)
        return

    uid = int(call.data.split(":")[1])
    data = user_data.get(uid)

    if not data:
        await call.answer("❌ Данные пользователя не найдены.", show_alert=True)
        return

    # ✅ Проверка по БАЗЕ, а не по user_data
    already_saved = await is_booking_already_saved( uid, data["booking_date"], data["selected_time"])
    if already_saved:
        await call.answer("Эта бронь уже была подтверждена.", show_alert=True)
        return

    data["confirmed_by_admin"] = True  # Помечаем как подтверждённую

    await save_user_info(uid, data)
    await call.answer("✅ Бронь подтверждена и записана!", show_alert=True)

    # Уведомление пользователю
    try:
        await call.bot.send_message(
            chat_id=uid,
            text="✅ Ваша бронь подтверждена администратором и успешно сохранена!"
        )
        await show_actions_to_user(uid, call.bot)
    except Exception as e:
        logging.warning(f"Не удалось уведомить пользователя {uid}: {e}")

    # Удаление inline-кнопки
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logging.warning(f"Не удалось удалить inline-кнопку: {e}")

@router.callback_query(F.data.startswith("admin_cancel:"))
async def handle_admin_cancel_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        await call.answer("⛔ Недостаточно прав.", show_alert=True)
        return

    uid = int(call.data.split(":")[1])
    logging.info(f"[admin_cancel_start] Админ: {call.from_user.id}, Целевой UID: {uid}")
    admin_cancel_requests[call.from_user.id] = uid

    await call.message.answer("✏️ Напишите причину отмены, которую мы отправим пользователю.")
    await state.set_state(CancelReason.waiting_for_reason)
    await call.answer()

@router.message(StateFilter(CancelReason.waiting_for_reason))
async def handle_admin_cancel_reason(message: Message, state: FSMContext):
    print("❗ ОБРАБОТЧИК ПРИЧИНЫ ВЫЗВАН")
    if message.from_user.id not in ADMINS:
        await message.answer("⛔ У вас нет доступа к этой функции.")
        return

    logging.info(f"[admin_cancel_reason] triggered by admin {message.from_user.id}")
    admin_id = message.from_user.id
    reason = message.text.strip()
    target_uid = admin_cancel_requests.pop(admin_id, None)
    logging.info(f"[admin_cancel_reason] target_uid: {target_uid}, reason: {reason}")

    if target_uid:
        await message.answer("✅ Причина получена. Отправляю сообщение пользователю.")
        try:
            await bot.send_message(target_uid, f"🚫 Ваша бронь была отменена администратором.\nПричина: {reason}")
            await show_actions_to_user(target_uid, bot)
            await bot.send_message(admin_id, "Пользователь уведомлён. Возвращаемся в меню.")
        except Exception as e:
            logging.error(f"[admin_cancel_reason] Failed to send message: {e}")
            await message.answer(f"❌ Ошибка отправки сообщения пользователю: {e}")
    else:
        await message.answer("⚠️ Не удалось найти пользователя для отмены.")

    await state.clear()

@router.callback_query(F.data == "admin_all_bookings")
async def handle_admin_all_bookings(call: CallbackQuery):
    bookings = await fetch_bookings_by_date()

    if not bookings:
        await call.message.answer("📭 Брони отсутствуют.")
        return

    # Группировка бронирований по датам
    bookings_by_date = {}
    for booking in bookings:
        booking_date = booking[0]  # booking_date
        if booking_date not in bookings_by_date:
            bookings_by_date[booking_date] = []
        bookings_by_date[booking_date].append(booking)

    # Создание клавиатуры с кнопками для каждой даты
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    for booking_date, booking_list in bookings_by_date.items():
        # Создание кнопки для каждой даты
        formatted_date = booking_date.strftime("%d.%m.%Y")
        button_text = f"Брони на {formatted_date}"
        button_data = f"view_bookings:{formatted_date}"
        
        markup.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=button_data)
        ])

    # Добавляем кнопку "Назад в админ-меню"
    markup.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад в админ-меню", callback_data="admin_menu")
    ])

    await call.message.answer("📋 Список бронирований по датам:", reply_markup=markup)
    await call.answer()


@router.callback_query(F.data == "admin_users")
async def handle_admin_users(call: CallbackQuery):
    users = await execute_query("SELECT id, nickname, phone FROM users ORDER BY id", fetch=True)

    if not users:
        await call.message.answer("❌ Нет зарегистрированных пользователей.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[])

    for uid, nickname, phone in users:
        # Убедимся, что это строки, чтобы не вылетало
        nickname = nickname or "❓"
        phone = phone or "❓"

        btn = InlineKeyboardButton(
            text=f"{nickname} | {phone}",
            callback_data=f"ban_user:{uid}"
        )
        markup.inline_keyboard.append([btn])  # одна кнопка — один ряд

    # Добавим кнопку "Назад"
    back_btn = InlineKeyboardButton(
        text="⬅ Назад в админ-меню",
        callback_data="admin_menu"
    )
    markup.inline_keyboard.append([back_btn])

    await call.message.answer("👥 Список пользователей:", reply_markup=markup)
    await call.answer()

@router.callback_query(F.data.startswith("ban_user:"))
async def handle_ban_unban_user(call: CallbackQuery):
    uid = int(call.data.split(":")[1])

    # Проверяем текущий статус пользователя
    user_data = await execute_query("SELECT is_banned, nickname FROM Users WHERE id = %s", (uid,), fetch=True)

    if not user_data:
        await call.answer("❌ Пользователь не найден.", show_alert=True)
        return

    # Получаем данные из кортежа
    is_banned = user_data[0][0]  # `user_data` — это кортеж, [0][0] будет брать первое значение в первой строке
    nickname = user_data[0][1]   # Аналогично для nickname

    # Если пользователь заблокирован — разблокируем, если нет — баним
    if is_banned:
        # Разбаниваем пользователя
        await unban_user(uid)
        await call.message.answer(f"✅ Пользователь {nickname} разблокирован.")
    else:
        # Баним пользователя
        await ban_user(uid)
        await call.message.answer(f"🚫 Пользователь {nickname} заблокирован.")

    await call.answer()



@router.callback_query(F.data == "admin_menu")
async def handle_admin_menu_entry(call: CallbackQuery):
    await show_admin_menu(call.message)
    await call.answer()

@router.callback_query(F.data == "search_user")
async def start_search_user(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSearch.waiting_for_query)
    await call.message.answer("🔎 Введите никнейм или номер телефона пользователя:")
    await call.answer()

@router.message(AdminSearch.waiting_for_query)
async def search_user_input(message: Message, state: FSMContext):
    query = message.text.strip()
    like_query = f"%{query}%"

    users = await search_users_by_query(message.text.strip())

    if not users:
        await message.answer("😕 Пользователи не найдены.")
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[])
        for uid, nickname, phone in users:
            btn = InlineKeyboardButton(
                text=f"{nickname or '❓'} | {phone or '❓'}",
                callback_data=f"ban_user:{uid}"
            )
            markup.inline_keyboard.append([btn])
        markup.inline_keyboard.append([
            InlineKeyboardButton(text="⬅ Назад в админ-меню", callback_data="admin_menu")
        ])
        await message.answer("👥 Результаты поиска:", reply_markup=markup)

    await state.clear()

@router.callback_query(F.data == "banned_users")
async def handle_banned_users(call: CallbackQuery):
    users = await get_banned_users()
    if not users:
        await call.message.answer("✅ Заблокированных пользователей нет.")
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[])
    for uid, nickname, phone, is_banned in users:
        nickname = nickname or "❓"
        phone = phone or "❓"
        status = "🔒 Заблокирован" if is_banned else "✅ Разблокирован"  # Можем выводить статус
        btn = InlineKeyboardButton(
            text=f"{nickname} | {phone} | {status}",
            callback_data=f"ban_user:{uid}"  # Будет использоваться для бана/разбана
        )
        markup.inline_keyboard.append([btn])

    markup.inline_keyboard.append([
        InlineKeyboardButton(text="⬅ Назад в админ-меню", callback_data="admin_menu")
    ])

    await call.message.answer("🚫 Заблокированные пользователи:", reply_markup=markup)
    await call.answer()

@router.callback_query(F.data.startswith("view_bookings:"))
async def handle_view_bookings_by_date(call: CallbackQuery):
    selected_date = call.data.split(":")[1]
    # Преобразуем строку в дату
    selected_date = datetime.strptime(selected_date, "%d.%m.%Y").date()

    # Получаем все брони на выбранную дату
    bookings = await execute_query(
        "SELECT booking_time, zone, computers, user_id FROM UserInfo WHERE booking_date = %s ORDER BY booking_time",
        (selected_date,),
        fetch=True
    )

    if not bookings:
        await call.message.answer(f"❌ Нет броней на {selected_date}.")
        return

    # Формируем ответ для пользователя
    text = f"📋 Брони на {selected_date.strftime('%d.%m.%Y')}:\n\n"
    for booking in bookings:
        booking_time = booking[0]
        zone = booking[1]
        computers = booking[2] if booking[2] else "Консоль"
        user_id = booking[3]
        user = await get_user_from_db(user_id)
        nickname = user.get("nickname", "???")
        
        text += f"⏰ Время: {booking_time}, Зона: {zone}, {computers} — Пользователь: {nickname}\n"

    await call.message.answer(text)
    await call.answer()
