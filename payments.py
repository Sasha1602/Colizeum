# payments.py

from yookassa import Configuration, Payment
import uuid
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

# Настройка ключей
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

def create_payment(amount_rub: float, description: str, return_url: str):
    try:
        payment = Payment.create({
            "amount": {
                "value": f"{amount_rub:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": return_url
            },
            "capture": True,
            "description": description
        }, uuid.uuid4())
        return payment.confirmation.confirmation_url, payment.id
    except Exception as e:
        print(f"[ERROR creating payment]: {e}")
        raise

def check_payment_status(payment_id):
    try:
        payment = Payment.find_one(payment_id)
        return payment.status
    except Exception as e:
        print(f"[ERROR checking payment]: {e}")
        return "error"
