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

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []

# Настройки Telegram Stars
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")

# Настройки ЮMoney
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "")
YOOMONEY_RECEIVER = os.getenv("YOOMONEY_RECEIVER", "")

# URL для WebApp
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-webapp-url.com")

# ========== ИМПОРТ БАЗЫ ДАННЫХ ==========
from database import init_db, save_ticket, update_ticket_status, get_user_by_message, get_ticket_status, get_old_pending_tickets, save_order

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== СОСТОЯНИЯ ==========
WAITING_IDEA, WAITING_QUESTION, WAITING_REPLY = range(3)

# Активные чаты
active_chats = {}
pending_payments = {}

ticket_counter = 0

def get_next_ticket_number():
    global ticket_counter
    ticket_counter += 1
    return ticket_counter

# ========== КЛАВИАТУРА ==========
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
    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=product_name,
            description=f"Покупка товара: {product_name}",
            payload=f"order_{uuid.uuid4().hex[:8]}",
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
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
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payment = update.message.successful_payment
    
    await save_order(
        user_id=user.id,
        username=user.username or user.full_name,
        order_id=payment.invoice_payload,
        product_name="Товар из каталога",
        amount=payment.total_amount,
        currency=payment.currency,
        payment_method="stars"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🛒 <b>Новый заказ оплачен через Stars!</b>\n\n"
                    f"👤 Пользователь: @{user.username or user.full_name}\n"
                    f"💰 Сумма: {payment.total_amount} {payment.currency}"
                ),
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    
    await update.message.reply_text(
        "✅ <b>Оплата прошла успешно!</b>\n\nСпасибо за покупку! Администратор свяжется с вами.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )

# ========== ОПЛАТА ЮMONEY ==========
def generate_yoomoney_link(amount: int, description: str, label: str) -> str:
    base_url = "https://yoomoney.ru/quickpay/confirm"
    params = {
        "receiver": YOOMONEY_RECEIVER,
        "quickpay-form": "shop",
        "targets": description[:100],
        "paymentType": "AC",
        "sum": amount,
        "label": label
    }
    param_str = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{base_url}?{param_str}"

async def check_yoomoney_payment(label: str) -> bool:
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
    label = f"order_{user_id}_{uuid.uuid4().hex[:8]}"
    payment_link = generate_yoomoney_link(product_price, product_name, label)
    
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

async def check_yoomoney_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    label = query.data.split("_", 2)[2]
    payment_info = pending_payments.get(label)
    
    if not payment_info:
        await query.edit_message_text("❌ Платёж не найден.")
        return
    
    is_paid = await check_yoomoney_payment(label)
    
    if is_paid:
        user_id = payment_info["user_id"]
        product_name = payment_info["product_name"]
        amount = payment_info["amount"]
        
        await save_order(
            user_id=user_id,
            username=query.from_user.username or query.from_user.full_name,
            order_id=label,
            product_name=product_name,
            amount=amount,
            currency="RUB",
            payment_method="yoomoney"
        )
        
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🛒 <b>Новый заказ оплачен через ЮMoney!</b>\n\n"
                        f"👤 Пользователь: @{query.from_user.username}\n"
                        f"🛍️ Товар: {product_name}\n"
                        f"💰 Сумма: {amount} руб."
                    ),
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        await query.edit_message_text(
            "✅ <b>Оплата подтверждена!</b>\n\nСпасибо за покупку!",
            parse_mode=ParseMode.HTML
        )
        del pending_payments[label]
    else:
        await query.edit_message_text(
            "⏳ <b>Оплата ещё не поступила</b>\n\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Проверить снова", callback_data=f"check_yoomoney_{label}")]
            ])
        )

# ========== ОБРАБОТЧИК WEBAPP ==========
async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = json.loads(update.effective_message.web_app_data.data)
    
    product_name = data.get("product_name", "Неизвестный товар")
    product_price = data.get("product_price", 0)
    payment_method = data.get("payment_method", "stars")
    
    if payment_method == "stars" and PROVIDER_TOKEN:
        await process_stars_payment(update, context, user.id, product_name, product_price)
    elif payment_method == "yoomoney" and YOOMONEY_TOKEN:
        await process_yoomoney_payment(update, context, user.id, product_name, product_price)
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Telegram Stars", callback_data=f"payopt_stars_{product_price}")],
            [InlineKeyboardButton("💳 ЮMoney (карта)", callback_data=f"payopt_yoomoney_{product_price}")]
        ])
        context.user_data["pending_product"] = {"name": product_name, "price": product_price}
        await update.message.reply_text(
            f"🛍️ <b>{product_name}</b>\n💰 {product_price} руб.\n\nВыберите способ оплаты:",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

async def payment_option_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    method = parts[1]
    product_price = int(parts[2])
    
    product = context.user_data.get("pending_product", {"name": "Товар", "price": product_price})
    
    if method == "stars":
        await process_stars_payment(update, context, query.from_user.id, product["name"], product["price"])
    elif method == "yoomoney":
        await process_yoomoney_payment(update, context, query.from_user.id, product["name"], product["price"])
    
    await query.message.delete()

# ========== ЧАТ С АДМИНИСТРАЦИЕЙ ==========
async def request_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or user.full_name
    
    if user_id in active_chats:
        await update.message.reply_text(
            "⏳ Вы уже находитесь в чате с администратором.\nИспользуйте /stopchat для завершения.",
            reply_markup=main_keyboard
        )
        return ConversationHandler.END
    
    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"accept_{user_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📞 <b>Запрос на связь</b>\n\n👤 @{username} (ID: <code>{user_id}</code>)",
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    
    await update.message.reply_text(
        "✅ Ваш запрос отправлен.\n⏳ Ожидайте ответа...",
        reply_markup=main_keyboard
    )
    return ConversationHandler.END

async def accept_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    
    user_id = int(query.data.split("_")[1])
    
    if user_id in active_chats:
        await query.edit_message_text("❌ Пользователь уже в чате.")
        return
    
    active_chats[user_id] = admin_id
    active_chats[admin_id] = user_id
    
    await query.edit_message_text(
        f"✅ Вы приняли запрос.\n💬 Сообщения будут пересылаться.\n📌 /stopchat для завершения.",
        parse_mode=ParseMode.HTML
    )
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 <b>Администратор подключился!</b>\n\n💬 Пишите ваш вопрос.\n📌 /stopchat для завершения.",
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    
    return ConversationHandler.END

async def reject_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = int(query.data.split("_")[1])
    await query.edit_message_text(f"❌ Запрос отклонён.", parse_mode=ParseMode.HTML)
    
    try:
        await context.bot.send_message(chat_id=user_id, text="❌ Администраторы сейчас не могут ответить.")
    except:
        pass

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    
    if sender_id not in active_chats:
        return
    
    receiver_id = active_chats[sender_id]
    
    if sender_id in ADMIN_IDS:
        prefix = "👨‍💼 Администратор"
    else:
        sender_name = update.effective_user.username or update.effective_user.full_name
        prefix = f"👤 {sender_name}"
    
    try:
        await context.bot.send_message(
            chat_id=receiver_id,
            text=f"💬 <b>{prefix}:</b>\n{update.message.text}",
            parse_mode=ParseMode.HTML
        )
    except:
        pass

async def stop_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text("❌ Вы не в чате.", reply_markup=main_keyboard)
        return
    
    partner_id = active_chats[user_id]
    del active_chats[user_id]
    del active_chats[partner_id]
    
    await update.message.reply_text("🔴 Чат завершён.", reply_markup=main_keyboard)
    
    try:
        await context.bot.send_message(chat_id=partner_id, text="🔴 <b>Чат завершён.</b>", parse_mode=ParseMode.HTML)
    except:
        pass

# ========== КОМАНДЫ ==========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет, {update.effective_user.full_name}!\n\nЯ бот для связи с командой.\nВыберите действие:",
        reply_markup=main_keyboard
    )

async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    hours = int(context.args[0]) if context.args else 0
    tickets = await get_old_pending_tickets(hours=hours)
    
    if not tickets:
        await update.message.reply_text(f"✅ Нет заявок, ожидающих более {hours} ч.")
        return
    
    text = f"📋 <b>Заявки в ожидании</b>"
    if hours > 0:
        text += f" (более {hours} ч.)"
    text += ":\n\n"
    
    for t in tickets[:15]:
        created = datetime.fromisoformat(t['created_at'])
        hours_ago = int((datetime.now() - created).total_seconds() / 3600)
        emoji = "🟢" if hours_ago < 12 else ("🟡" if hours_ago < 24 else "🔴")
        text += f"{emoji} #{t['id']} ({t['type']}) — {hours_ago} ч. назад\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ========== НАПОМИНАНИЯ ==========
async def check_pending_tickets(context: ContextTypes.DEFAULT_TYPE):
    tickets = await get_old_pending_tickets(hours=24)
    
    if tickets:
        count = len(tickets)
        text = f"⚠️ <b>Внимание!</b>\n\n🔴 <b>{count}</b> заявок ожидают более 24 часов:\n\n"
        
        for t in tickets[:10]:
            created = datetime.fromisoformat(t['created_at'])
            hours_ago = int((datetime.now() - created).total_seconds() / 3600)
            text += f"• #{t['id']} ({t['type']}) — {hours_ago} ч. назад\n"
        
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text, parse_mode=ParseMode.HTML)
            except:
                pass

# ========== ЗАЯВКИ ==========
async def idea_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Опишите свою идею ниже:", reply_markup=ReplyKeyboardRemove())
    return WAITING_IDEA

async def question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ Опишите ваш вопрос ниже:", reply_markup=ReplyKeyboardRemove())
    return WAITING_QUESTION

async def process_idea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    ticket_num = get_next_ticket_number()
    message_text = update.message.text
    message_id = update.message.message_id
    
    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{message_id}_{ticket_num}"),
            InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{message_id}_{ticket_num}")
        ]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"💡 <b>НОВАЯ ИДЕЯ #{ticket_num}</b>\n\n👤 @{username}\n\n📄 {message_text}",
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    
    await save_ticket(user_id, username, message_id, "idea", message_text)
    await update.message.reply_text(
        f"✅ Идея отправлена!\n📌 Номер заявки: <b>#{ticket_num}</b>",
        reply_markup=main_keyboard,
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def process_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    ticket_num = get_next_ticket_number()
    message_text = update.message.text
    message_id = update.message.message_id
    
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{message_id}_{ticket_num}")]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"❓ <b>НОВЫЙ ВОПРОС #{ticket_num}</b>\n\n👤 @{username}\n\n📄 {message_text}",
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML
            )
        except:
            pass
    
    await save_ticket(user_id, username, message_id, "question", message_text)
    await update.message.reply_text(
        f"✅ Вопрос отправлен!\n📌 Номер заявки: <b>#{ticket_num}</b>",
        reply_markup=main_keyboard,
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

async def reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    
    parts = query.data.split("_")
    message_id = int(parts[1])
    ticket_num = parts[2] if len(parts) > 2 else "?"
    
    context.user_data["reply_to_msg"] = message_id
    context.user_data["ticket_num"] = ticket_num
    
    await query.message.reply_text(f"✏️ Напишите ответ (заявка #{ticket_num}):")
    return WAITING_REPLY

async def send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    
    original_msg_id = context.user_data.get("reply_to_msg")
    ticket_num = context.user_data.get("ticket_num", "?")
    
    if not original_msg_id:
        await update.message.reply_text("❌ Ошибка")
        return ConversationHandler.END
    
    user_id, _ = await get_user_by_message(original_msg_id)
    admin_name = update.effective_user.username or update.effective_user.full_name
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📬 <b>Ответ на вопрос #{ticket_num}</b>\n\n👤 @{admin_name}\n📝 {update.message.text}",
                parse_mode=ParseMode.HTML
            )
            await update_ticket_status(original_msg_id, "answered")
            await update.message.reply_text(f"✅ Ответ отправлен!")
        except:
            pass
    
    return ConversationHandler.END

async def approve_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        return
    
    parts = query.data.split("_")
    message_id = int(parts[1])
    ticket_num = parts[2] if len(parts) > 2 else "?"
    
    user_id, _ = await get_user_by_message(message_id)
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 <b>Идея #{ticket_num} ОДОБРЕНА!</b>",
                parse_mode=ParseMode.HTML
            )
            await update_ticket_status(message_id, "approved")
            await query.edit_message_reply_markup(reply_markup=None)
        except:
            pass

async def reject_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        return
    
    parts = query.data.split("_")
    message_id = int(parts[1])
    ticket_num = parts[2] if len(parts) > 2 else "?"
    
    user_id, _ = await get_user_by_message(message_id)
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📋 <b>Идея #{ticket_num} отклонена.</b>",
                parse_mode=ParseMode.HTML
            )
            await update_ticket_status(message_id, "rejected")
            await query.edit_message_reply_markup(reply_markup=None)
        except:
            pass

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=main_keyboard)
    return ConversationHandler.END

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Платежи
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, webapp_data_handler))
    application.add_handler(CallbackQueryHandler(payment_option_handler, pattern="^payopt_"))
    application.add_handler(CallbackQueryHandler(check_yoomoney_callback, pattern="^check_yoomoney_"))
    
    # Команды
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("pending", cmd_pending))
    application.add_handler(CommandHandler("stopchat", stop_chat_command))
    
    # Чат
    application.add_handler(MessageHandler(filters.Regex("^📞 Связь с администрацией$"), request_chat))
    application.add_handler(CallbackQueryHandler(accept_chat, pattern="^accept_"))
    application.add_handler(CallbackQueryHandler(reject_chat, pattern="^reject_"))
    
    # Заявки
    idea_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💡 Отправить идею$"), idea_start)],
        states={WAITING_IDEA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_idea)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    question_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❓ Задать вопрос$"), question_start)],
        states={WAITING_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_question)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reply_button, pattern="^reply_")],
        states={WAITING_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_reply)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(idea_conv)
    application.add_handler(question_conv)
    application.add_handler(reply_conv)
    application.add_handler(CallbackQueryHandler(approve_button, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(reject_button, pattern="^reject_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))
    
    # Напоминания
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_pending_tickets, interval=3600, first=10)
    
    print("✅ Бот запущен!")
    application.run_polling()
