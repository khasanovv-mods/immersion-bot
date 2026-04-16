#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import json
import uuid
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, WebAppInfo, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler, ContextTypes, PreCheckoutQueryHandler
from telegram.constants import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []

# Настройки Telegram Stars
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")  # Токен от BotFather для Stars

# Настройки ЮMoney
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "")  # API токен ЮMoney
YOOMONEY_RECEIVER = os.getenv("YOOMONEY_RECEIVER", "")  # Номер кошелька

# URL для WebApp
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-webapp-url.com")

from database import init_db, save_ticket, update_ticket_status, get_user_by_message, get_ticket_status, get_old_pending_tickets, save_order

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
WAITING_IDEA, WAITING_QUESTION, WAITING_REPLY = range(3)
WAITING_YOOMONEY_AMOUNT = 3  # Для ввода суммы пополнения

# Активные чаты
active_chats = {}

# Временное хранилище для платежей ЮMoney
pending_payments = {}

ticket_counter = 0

def get_next_ticket_number():
    global ticket_counter
    ticket_counter += 1
    return ticket_counter

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Отправить идею")],
        [KeyboardButton(text="❓ Задать вопрос")],
        [KeyboardButton(text="🛍️ Каталог товаров", web_app=WebAppInfo(url=WEBAPP_URL))],
        [KeyboardButton(text="📞 Связь с администрацией")]
    ],
    resize_keyboard=True
)

# ========== ОПЛАТА TELEGRAM STARS ==========

async def process_stars_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, product_name: str, product_price: int):
    """Создание платежа через Telegram Stars"""
    try:
        # Создаём счёт
        await context.bot.send_invoice(
            chat_id=user_id,
            title=product_name,
            description=f"Покупка товара: {product_name}",
            payload=f"order_{uuid.uuid4().hex[:8]}",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",  # Telegram Stars
            prices=[LabeledPrice(product_name, product_price)],
            start_parameter="catalog",
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False
        )
        logger.info(f"✅ Счёт Stars отправлен пользователю {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка создания счёта Stars: {e}")
        return False

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение возможности оплаты"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка успешной оплаты Stars"""
    user = update.effective_user
    payment = update.message.successful_payment
    
    order_id = payment.invoice_payload
    amount = payment.total_amount
    currency = payment.currency
    
    # Сохраняем заказ в БД
    await save_order(
        user_id=user.id,
        username=user.username or user.full_name,
        order_id=order_id,
        product_name="Товар из каталога",
        amount=amount,
        currency=currency,
        payment_method="stars"
    )
    
    # Уведомляем админов
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🛒 <b>Новый заказ оплачен через Stars!</b>\n\n"
                    f"👤 Пользователь: @{user.username or user.full_name} (ID: <code>{user.id}</code>)\n"
                    f"🆔 Заказ: {order_id}\n"
                    f"💰 Сумма: {amount} {currency}\n\n"
                    f"✅ Оплата подтверждена, свяжитесь с пользователем."
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")
    
    await update.message.reply_text(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Спасибо за покупку! Администратор свяжется с вами в ближайшее время.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )
    logger.info(f"✅ Платёж Stars от {user.id} на сумму {amount} обработан")

# ========== ОПЛАТА ЮMONEY ==========

def generate_yoomoney_link(amount: int, description: str, label: str) -> str:
    """Генерация ссылки на оплату через ЮMoney"""
    base_url = "https://yoomoney.ru/quickpay/confirm"
    params = {
        "receiver": YOOMONEY_RECEIVER,
        "quickpay-form": "shop",
        "targets": description[:100],
        "paymentType": "AC",  # AC = банковская карта, PC = кошелёк ЮMoney
        "sum": amount,
        "label": label
    }
    param_str = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{base_url}?{param_str}"

async def check_yoomoney_payment(label: str) -> bool:
    """Проверка статуса платежа через API ЮMoney"""
    if not YOOMONEY_TOKEN:
        return False
    
    try:
        import aiohttp
        headers = {"Authorization": f"Bearer {YOOMONEY_TOKEN}"}
        url = "https://yoomoney.ru/api/operation-history"
        data = {"label": label, "records": 10}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=data) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "operations" in result:
                        for op in result["operations"]:
                            if op.get("label") == label and op.get("status") == "success":
                                return True
        return False
    except Exception as e:
        logger.error(f"Ошибка проверки платежа ЮMoney: {e}")
        return False

async def process_yoomoney_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, product_name: str, product_price: int):
    """Создание ссылки на оплату через ЮMoney"""
    label = f"order_{user_id}_{uuid.uuid4().hex[:8]}"
    payment_link = generate_yoomoney_link(product_price, product_name, label)
    
    # Сохраняем информацию о платеже
    pending_payments[label] = {
        "user_id": user_id,
        "product_name": product_name,
        "amount": product_price,
        "created_at": datetime.now()
    }
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_link)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data=f"check_yoomoney_{label}")]
    ])
    
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"💳 <b>Оплата через ЮMoney</b>\n\n"
            f"🛍️ Товар: {product_name}\n"
            f"💰 Сумма: {product_price} руб.\n\n"
            f"Нажмите кнопку ниже для оплаты. После оплаты нажмите «✅ Я оплатил»."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    logger.info(f"✅ Ссылка ЮMoney создана для {user_id}")

async def check_yoomoney_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия «Я оплатил»"""
    query = update.callback_query
    await query.answer()
    
    label = query.data.split("_", 2)[2]
    payment_info = pending_payments.get(label)
    
    if not payment_info:
        await query.edit_message_text("❌ Платёж не найден или истекло время ожидания.")
        return
    
    # Проверяем оплату
    is_paid = await check_yoomoney_payment(label)
    
    if is_paid:
        user_id = payment_info["user_id"]
        product_name = payment_info["product_name"]
        amount = payment_info["amount"]
        
        # Сохраняем в БД
        await save_order(
            user_id=user_id,
            username=query.from_user.username or query.from_user.full_name,
            order_id=label,
            product_name=product_name,
            amount=amount,
            currency="RUB",
            payment_method="yoomoney"
        )
        
        # Уведомляем админов
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🛒 <b>Новый заказ оплачен через ЮMoney!</b>\n\n"
                        f"👤 Пользователь: @{query.from_user.username} (ID: <code>{user_id}</code>)\n"
                        f"🛍️ Товар: {product_name}\n"
                        f"💰 Сумма: {amount} руб.\n\n"
                        f"✅ Платёж подтверждён!"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления: {e}")
        
        await query.edit_message_text(
            f"✅ <b>Оплата подтверждена!</b>\n\n"
            f"Спасибо за покупку! Администратор свяжется с вами.",
            parse_mode=ParseMode.HTML
        )
        del pending_payments[label]
    else:
        await query.edit_message_text(
            f"⏳ <b>Оплата ещё не поступила</b>\n\n"
            f"Попробуйте проверить позже или свяжитесь с администратором.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Проверить снова", callback_data=f"check_yoomoney_{label}")]
            ])
        )

# ========== ОБРАБОТЧИК WEBAPP ==========

async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает данные из WebApp (каталог)"""
    user = update.effective_user
    data = json.loads(update.effective_message.web_app_data.data)
    
    product_name = data.get("product_name", "Неизвестный товар")
    product_price = data.get("product_price", 0)
    product_id = data.get("product_id", "?")
    payment_method = data.get("payment_method", "stars")  # stars или yoomoney
    
    if payment_method == "stars" and PROVIDER_TOKEN:
        success = await process_stars_payment(update, context, user.id, product_name, product_price)
        if not success:
            await update.message.reply_text(
                "❌ Не удалось создать счёт. Попробуйте позже или выберите другой способ оплаты.",
                reply_markup=main_keyboard
            )
    elif payment_method == "yoomoney" and YOOMONEY_TOKEN:
        await process_yoomoney_payment(update, context, user.id, product_name, product_price)
    else:
        # Если способ оплаты не указан — спрашиваем
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Telegram Stars", callback_data=f"payopt_stars_{product_id}_{product_price}")],
            [InlineKeyboardButton("💳 ЮMoney (карта)", callback_data=f"payopt_yoomoney_{product_id}_{product_price}")]
        ])
        await update.message.reply_text(
            f"🛍️ <b>{product_name}</b>\n💰 {product_price} руб.\n\nВыберите способ оплаты:",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

async def payment_option_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора способа оплаты"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    method = parts[1]  # stars или yoomoney
    product_id = parts[2]
    product_price = int(parts[3])
    
    # Получаем название товара (можно из БД или временного хранилища)
    product_name = f"Товар #{product_id}"
    
    if method == "stars":
        await process_stars_payment(update, context, query.from_user.id, product_name, product_price)
        await query.message.delete()
    elif method == "yoomoney":
        await process_yoomoney_payment(update, context, query.from_user.id, product_name, product_price)
        await query.message.delete()

# ========== КОМАНДЫ ==========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет, {update.effective_user.full_name}!\n\n"
        "Я бот для связи с командой.\nВыберите действие:",
        reply_markup=main_keyboard
    )

async def cmd_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Открыть каталог по команде /catalog"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Открыть каталог", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await update.message.reply_text(
        "🛒 Нажмите кнопку ниже, чтобы открыть каталог товаров:",
        reply_markup=keyboard
    )

# ========== ЧАТ ==========
# ... (весь код чата остаётся без изменений из предыдущей версии) ...

# ========== ЗАЯВКИ ==========
# ... (весь код заявок остаётся без изменений) ...

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    loop.run_until_complete(init_db())
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Платёжные обработчики
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data_handler))
    application.add_handler(CallbackQueryHandler(payment_option_handler, pattern="^payopt_"))
    application.add_handler(CallbackQueryHandler(check_yoomoney_callback, pattern="^check_yoomoney_"))
    
    # Команды
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("catalog", cmd_catalog))
    # ... остальные хендлеры ...
    
    print("✅ Бот запущен с оплатой Stars и ЮMoney!")
    application.run_polling()
