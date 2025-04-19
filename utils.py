from datetime import datetime, timedelta

def validate_phone(phone):
    """Валидация номера телефона."""
    if phone.startswith('+7') and len(phone) == 12:
        return True
    elif phone.startswith('8') and len(phone) == 11:
        return True
    return False

def get_min_max_dates():
    """Получение минимальной и максимальной даты для бронирования."""
    now = datetime.now()
    max_date = now + timedelta(days=7)
    return now, max_date