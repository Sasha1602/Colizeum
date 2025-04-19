import asyncio
from datetime import datetime, timedelta
import aiomysql
from config import DB_CONFIG
import logging

db_pool = None

def set_db_pool(pool):
    global db_pool
    db_pool = pool

async def create_db_connection():
    """Создает асинхронное подключение к базе данных."""
    try:
        conn = await aiomysql.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            db=DB_CONFIG['db'],
            autocommit=True
        )
        return conn
    except Exception as e:
        logging.error(f"Ошибка подключения к базе данных: {e}")
        raise

async def execute_query(query, params=None, fetch=False):
    async with db_pool.acquire() as conn:
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                if fetch:
                    return await cursor.fetchall()
                await conn.commit()
        except Exception as e:
            logging.error(f"Ошибка выполнения запроса: {query} | Ошибка: {e}")
            return None

async def delete_booking(booking_id):
    query = "DELETE FROM UserInfo WHERE id = %s"
    await execute_query(query, (booking_id,))

async def delete_all_user_bookings(phone_number, nickname):
    query = "DELETE FROM UserInfo WHERE phone = %s AND nickname = %s"
    await execute_query(query, (phone_number, nickname))

async def fetch_user_bookings(phone_number, nickname):
    query = """
        SELECT id, booking_date, booking_time, zone, computers 
        FROM UserInfo 
        WHERE phone = %s AND nickname = %s
    """
    return await execute_query(query, (phone_number, nickname), fetch=True)

async def format_date(date_str):
    try:
        return datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError as e:
        logging.error(f"Ошибка преобразования даты: {date_str} | {e}")
        return None

async def check_availability(computer_ids, booking_date, booking_time, duration_hours):
    if not computer_ids:
        return True, []

    formatted_date = await format_date(booking_date)
    if not formatted_date:
        logging.error("Некорректная дата, не удалось проверить доступность.")
        return False, []

    query = f"""
        SELECT booking_time, duration, computers
        FROM UserInfo
        WHERE booking_date = %s
        AND (
            {" OR ".join(["FIND_IN_SET(%s, computers)" for _ in computer_ids])}
        )
    """
    params = (formatted_date, *map(str, computer_ids))
    existing_bookings = await execute_query(query, params, fetch=True)

    try:
        new_start = datetime.strptime(f"{formatted_date} {booking_time}", "%Y-%m-%d %H:%M")
        new_end = new_start + timedelta(hours=duration_hours)

        conflicts = []

        for existing_time_str, existing_duration, computers_str in existing_bookings:
            existing_start = datetime.strptime(f"{formatted_date} {str(existing_time_str)[:5]}", "%Y-%m-%d %H:%M")
            existing_end = existing_start + timedelta(hours=int(existing_duration))

            if max(new_start, existing_start) < min(new_end, existing_end):
                booked_computers = [int(x.strip()) for x in computers_str.split(',') if x.strip().isdigit()]
                for comp in booked_computers:
                    if comp in computer_ids and (comp, existing_end.strftime("%H:%M")) not in conflicts:
                        conflicts.append((comp, existing_end.strftime("%H:%M")))

        return len(conflicts) == 0, conflicts

    except Exception as e:
        logging.error(f"[check_availability] Ошибка при проверке пересечений: {e}")
        return False, []

async def format_date(date_str):
    """
    Преобразует строку даты из формата 'DD.MM.YYYY' в 'YYYY-MM-DD'
    """
    try:
        return datetime.strptime(date_str, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        logging.error(f"Ошибка преобразования даты: {date_str}")
        return None

async def save_user_info(uid, data):
    """
    Сохраняет информацию о бронировании в базу данных.
    :param uid: ID пользователя в Telegram.
    :param data: Словарь с данными пользователя.
    """
    formatted_date = await format_date(data['booking_date'])

    if not formatted_date:
        logging.error("Некорректная дата бронирования.")
        return

    query = """
        INSERT INTO UserInfo (
            user_id, nickname, phone, zone,
            computer_count, booking_date, booking_time,
            computers, duration
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        uid,
        data['nikname'],
        data['telefhone'],
        data['selected_zone'],
        data['number_of_computers'],
        formatted_date,
        data['selected_time'],
        ', '.join(map(str, data['selected_computers'])),
        data['duration']
    )

    await execute_query(query, params)

async def check_user_in_db(uid):
    """
    Проверяет, существует ли пользователь в базе данных по его uid.
    :param uid: ID пользователя в Telegram.
    :return: True, если пользователь существует, иначе False.
    """
    query = "SELECT COUNT(*) FROM Users WHERE user_id = %s"
    result = await execute_query(query, (uid,), fetch=True)
    if result:
        (count,) = result[0]
        return count > 0
    return False

async def register_user(uid, phone_number, nickname):
    """
    Регистрирует нового пользователя в базе данных.
    :param uid: ID пользователя в Telegram.
    :param phone_number: Номер телефона пользователя.
    :param nickname: Никнейм пользователя.
    """
    query = """
        INSERT INTO Users (user_id, phone, nickname, registration_date)
        VALUES (%s, %s, %s, NOW())
    """
    await execute_query(query, (uid, phone_number, nickname))

async def get_user_from_db(uid):
    """
    Загружает данные пользователя из базы данных.
    :param uid: ID пользователя в Telegram.
    :return: Словарь с данными пользователя или None, если не найден.
    """
    query = "SELECT nickname, phone FROM Users WHERE user_id = %s"
    result = await execute_query(query, (uid,), fetch=True)
    if result:
        return {"nickname": result[0][0], "phone": result[0][1]}
    return None

async def fetch_user_bookings_by_uid(uid):
    query = """
        SELECT id, booking_date, booking_time, zone, computers, duration
        FROM UserInfo
        WHERE user_id = %s AND is_paid = FALSE
    """
    return await execute_query(query, (uid,), fetch=True)

async def delete_booking_by_id(booking_id):
    query = "DELETE FROM UserInfo WHERE id = %s"
    await execute_query(query, (booking_id,))

async def delete_all_bookings_by_uid(uid):
    query = "DELETE FROM UserInfo WHERE user_id = %s"
    await execute_query(query, (uid,))

async def mark_booking_as_paid(booking_id):
    query = "UPDATE UserInfo SET is_paid = TRUE WHERE id = %s"
    await execute_query(query, (booking_id,))

async def set_payment_id(booking_id, payment_id):
    query = "UPDATE UserInfo SET payment_id = %s WHERE id = %s"
    await execute_query(query, (payment_id, booking_id))

async def get_payment_id_by_booking(booking_id):
    query = "SELECT payment_id FROM UserInfo WHERE id = %s"
    result = await execute_query(query, (booking_id,), fetch=True)
    if result:
        return result[0][0]
    return None

async def fetch_paid_bookings(uid):
    query = """
        SELECT booking_date, booking_time, zone, computers
        FROM UserInfo
        WHERE user_id = %s AND is_paid = TRUE
        ORDER BY booking_date, booking_time
    """
    return await execute_query(query, (uid,), fetch=True)

async def fetch_unpaid_bookings(uid):
    query = """
        SELECT id, booking_date, booking_time, zone, computers
        FROM UserInfo
        WHERE user_id = %s AND is_paid = 0
    """
    return await execute_query(query, (uid,), fetch=True)

async def remove_expired_bookings():
    while True:
        try:
            now = datetime.now()
            formatted_now = now.strftime('%Y-%m-%d %H:%M:%S')

            await execute_query("""
                DELETE FROM UserInfo 
                WHERE TIMESTAMP(booking_date, booking_time) < %s
            """, (formatted_now,))
            
            logging.info("[✓] Удалены просроченные брони")
        except Exception as e:
            logging.error(f"[✗] Ошибка при удалении просроченных броней: {e}")

        await asyncio.sleep(300)  # каждые 5 минут

async def get_booking_info(booking_id):
    query = """
        SELECT booking_date, booking_time, zone, computers, duration
        FROM UserInfo
        WHERE id = %s
    """
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query, (booking_id,))
                return await cursor.fetchone()
    except Exception as e:
        logging.error(f"Ошибка получения данных брони: {e}")
        return None
    
async def is_booking_already_saved(user_id, date_str, time_str):
    try:
        # преобразуем дату в формат YYYY-MM-DD
        date_obj = datetime.strptime(date_str, "%d.%m.%Y").date()
        query = """
            SELECT COUNT(*) FROM UserInfo 
            WHERE user_id = %s AND booking_date = %s AND booking_time = %s
        """
        result = await execute_query(query, (user_id, date_obj, time_str), fetch=True)
        return result[0][0] > 0 if result else False
    except Exception as e:
        logging.error(f"[CHECK BOOKING] Ошибка проверки брони: {e}")
        return False

async def get_all_users():
    query = "SELECT id, nickname, phone FROM users ORDER BY id;"
    return await execute_query(query, fetch=True)

async def get_all_bookings():
    query = """
        SELECT user_id, booking_date, booking_time, zone, computers, duration
        FROM userinfo
        ORDER BY booking_date, booking_time;
    """
    return await execute_query(query, fetch=True)

async def ban_user(uid):
    query = "UPDATE Users SET is_banned = 1 WHERE id = %s"
    await execute_query(query, (uid,))


async def is_user_banned(user_id):
    try:
        result = await execute_query("SELECT is_banned FROM users WHERE user_id = %s", (user_id,), fetch=True)
        return result[0][0] if result else False
    except Exception as e:
        logging.error(f"[BAN CHECK] Ошибка при проверке: {e}")
        return False
    
async def get_banned_users():
    query = "SELECT id, nickname, phone, is_banned FROM Users WHERE is_banned = 1"
    return await execute_query(query, fetch=True)

async def unban_user(uid):
    query = "UPDATE Users SET is_banned = 0 WHERE id = %s"
    await execute_query(query, (uid,))

async def search_users_by_query(query_text):
    """
    Выполняет поиск пользователей по нику или телефону (поиск по подстроке).
    :param query_text: строка поиска (часть ника или телефона)
    :return: список найденных пользователей (id, nickname, phone)
    """
    like_query = f"%{query_text}%"
    sql = "SELECT id, nickname, phone FROM Users WHERE nickname LIKE %s OR phone LIKE %s"
    return await execute_query(sql, (like_query, like_query), fetch=True)

async def fetch_bookings_by_date():
    query = """
        SELECT booking_date, booking_time, zone, computers, user_id
        FROM UserInfo
        ORDER BY booking_date, booking_time
    """
    return await execute_query(query, fetch=True)