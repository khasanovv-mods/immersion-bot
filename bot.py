#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler, ContextTypes
from telegram.constants import ParseMode

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []

# Настройки ЮMoney
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "")
YOOMONEY_RECEIVER = os.getenv("YOOMONEY_RECEIVER", "")

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

# ========== КАТАЛОГ ТОВАРОВ ==========
CATALOG = {
    "futbolki": {
        "name": "👕 Футболки",
        "products": {
            "fut_1": {"name": "Футболка «Классика»", "desc": "Хлопок 100%, размеры S-XXL", "price": 1500},
            "fut_2": {"name": "Футболка «Премиум»", "desc": "Органический хлопок", "price": 2200},
        }
    },
    "hoodie": {
        "name": "🧥 Худи",
        "products": {
            "hoodie_1": {"name": "Худи «Базовое»", "desc": "Флис, капюшон", "price": 3500},
            "hoodie_2": {"name": "Худи «Оверсайз»", "desc": "Свободный крой", "price": 3900},
        }
    },
    "caps": {
        "name": "🧢 Кепки",
        "products": {
            "cap_1": {"name": "Кепка «Снэпбэк»", "desc": "Регулируемый размер", "price": 1200},
            "cap_2": {"name": "Кепка «Бейсболка»", "desc": "Классическая", "price": 900},
        }
    }
}

# ========== КЛАВИАТУРА ==========
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Отправить идею")],
        [KeyboardButton(text="❓ Задать вопрос")],
        [KeyboardButton(text="🛍️ Каталог")],
        [KeyboardButton(text="📞 Связь с администрацией")]
    ],
    resize_keyboard=True
)

# ========== КАТАЛОГ ==========
async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for cat_id, cat_data in CATALOG.items():
        keyboard.append([InlineKeyboardButton(cat_data["name"], callback_data=f"cat_{cat_id}")])
    
    await update.message.reply_text(
        "🛍️ <b>Каталог товаров</b>\n\nВыберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def show_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    cat_id = query.data.split("_")[1]
    cat_data = CATALOG.get(cat_id)
    if not cat_data:
        return
    
    keyboard = []
    for prod_id, prod in cat_data["products"].items():
        keyboard.append([InlineKeyboardButton(
            f"{prod['name']} — {prod['price']} ₽",
            callback_data=f"buy_{cat_id}_{prod_id}"
        )])
    keyboard.append([InlineKeyboardButton("« Назад к категориям", callback_data="back_to_catalog")])
    
    await query.edit_message_text(
        f"🛍️ <b>{cat_data['name']}</b>\n\nВыберите товар:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Прямая покупка товара — создаёт ссылку на оплату"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    parts = query.data.split("_")
    cat_id, prod_id = parts[1], parts[2]
    
    product = CATALOG.get(cat_id, {}).get("products", {}).get(prod_id)
    if not product:
        await query.answer("❌ Товар не найден", show_alert=True)
        return
    
    # Создаём заказ
    order_id = f"order_{user_id}_{uuid.uuid4().hex[:8]}"
    payment_link = generate_yoomoney_link(product["price"], product["name"], order_id)
    
    # Сохраняем информацию о платеже
    pending_payments[order_id] = {
        "user_id": user_id,
        "product_name": product["name"],
        "price": product["price"],
        "created_at": datetime.now()
    }
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_link)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid_{order_id}")]
    ])
    
    await query.edit_message_text(
        f"🛍️ <b>{product['name']}</b>\n\n"
        f"📝 {product['desc']}\n"
        f"💰 Цена: <b>{product['price']} ₽</b>\n\n"
        f"Нажмите кнопку ниже для оплаты через ЮMoney.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

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
    return f"{base_url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    order_id = query.data.split("_")[1]
    payment_info = pending_payments.get(order_id)
    
    if not payment_info:
        await query.edit_message_text("❌ Заказ не найден.")
        return
    
    is_paid = await check_yoomoney_payment(order_id)
    
    if is_paid:
        user_id = payment_info["user_id"]
        product_name = payment_info["product_name"]
        price = payment_info["price"]
        
        await save_order(user_id, query.from_user.username or query.from_user.full_name, order_id, product_name, price, "RUB", "yoomoney")
        
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🛒 <b>Новый заказ!</b>\n\n👤 @{query.from_user.username}\n🛍️ {product_name}\n💰 {price} ₽",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        await query.edit_message_text("✅ <b>Оплата подтверждена!</b>\n\nСпасибо за заказ! Администратор свяжется с вами.", parse_mode=ParseMode.HTML)
        del pending_payments[order_id]
    else:
        await query.edit_message_text(
            "⏳ <b>Оплата ещё не поступила</b>\n\nПопробуйте позже.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Проверить снова", callback_data=f"paid_{order_id}")]])
        )

async def check_yoomoney_payment(label: str) -> bool:
    if not YOOMONEY_TOKEN:
        return False
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {YOOMONEY_TOKEN}"}
            async with session.post("https://yoomoney.ru/api/operation-history", headers=headers, data={"label": label, "records": 10}) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    for op in result.get("operations", []):
                        if op.get("label") == label and op.get("status") == "success":
                            return True
        return False
    except:
        return False

# ========== НАВИГАЦИЯ ==========
async def back_to_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton(cat["name"], callback_data=f"cat_{cat_id}")] for cat_id, cat in CATALOG.items()]
    await query.edit_message_text("🛍️ <b>Каталог товаров</b>\n\nВыберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

# ========== ЧАТ С АДМИНИСТРАЦИЕЙ ==========
async def request_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or user.full_name
    
    if user_id in active_chats:
        await update.message.reply_text("⏳ Вы уже в чате. /stopchat для завершения.", reply_markup=main_keyboard)
        return ConversationHandler.END
    
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять", callback_data=f"accept_{user_id}"),
         InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{user_id}")]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=f"📞 <b>Запрос на связь</b>\n\n👤 @{username} (ID: <code>{user_id}</code>)", reply_markup=admin_kb, parse_mode=ParseMode.HTML)
        except:
            pass
    
    await update.message.reply_text("✅ Запрос отправлен. Ожидайте...", reply_markup=main_keyboard)
    return ConversationHandler.END

async def accept_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        return
    user_id = int(query.data.split("_")[1])
    if user_id in active_chats:
        await query.edit_message_text("❌ Уже в чате")
        return
    active_chats[user_id] = admin_id
    active_chats[admin_id] = user_id
    await query.edit_message_text(f"✅ Вы приняли запрос.\n💬 Сообщения пересылаются.\n/stopchat для завершения.", parse_mode=ParseMode.HTML)
    try:
        await context.bot.send_message(chat_id=user_id, text="🎉 <b>Администратор подключился!</b>\n\n💬 Пишите ваш вопрос.\n/stopchat для завершения.", parse_mode=ParseMode.HTML)
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
    prefix = "👨‍💼 Администратор" if sender_id in ADMIN_IDS else f"👤 {update.effective_user.username or update.effective_user.full_name}"
    try:
        await context.bot.send_message(chat_id=receiver_id, text=f"💬 <b>{prefix}:</b>\n{update.message.text}", parse_mode=ParseMode.HTML)
    except:
        pass
    raise ConversationHandler.END

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
    await update.message.reply_text(f"👋 Привет, {update.effective_user.full_name}!\n\nЯ бот для связи с командой.\nВыберите действие:", reply_markup=main_keyboard)

async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    hours = int(context.args[0]) if context.args else 0
    tickets = await get_old_pending_tickets(hours=hours)
    if not tickets:
        await update.message.reply_text(f"✅ Нет заявок, ожидающих более {hours} ч.")
        return
    text = f"📋 <b>Заявки в ожидании</b>" + (f" (более {hours} ч.)" if hours > 0 else "") + ":\n\n"
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
        text = f"⚠️ <b>Внимание!</b>\n\n🔴 <b>{len(tickets)}</b> заявок ожидают более 24 часов:\n\n"
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
    message_id = update.message.message_id
    
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{message_id}_{ticket_num}"),
         InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{message_id}_{ticket_num}")]
    ])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=f"💡 <b>НОВАЯ ИДЕЯ #{ticket_num}</b>\n\n👤 @{username}\n\n📄 {update.message.text}", reply_markup=admin_kb, parse_mode=ParseMode.HTML)
        except:
            pass
    
    await save_ticket(user_id, username, message_id, "idea", update.message.text)
    await update.message.reply_text(f"✅ Идея отправлена!\n📌 Номер заявки: <b>#{ticket_num}</b>", reply_markup=main_keyboard, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def process_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    ticket_num = get_next_ticket_number()
    message_id = update.message.message_id
    
    admin_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Ответить", callback_data=f"reply_{message_id}_{ticket_num}")]])
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=f"❓ <b>НОВЫЙ ВОПРОС #{ticket_num}</b>\n\n👤 @{username}\n\n📄 {update.message.text}", reply_markup=admin_kb, parse_mode=ParseMode.HTML)
        except:
            pass
    
    await save_ticket(user_id, username, message_id, "question", update.message.text)
    await update.message.reply_text(f"✅ Вопрос отправлен!\n📌 Номер заявки: <b>#{ticket_num}</b>", reply_markup=main_keyboard, parse_mode=ParseMode.HTML)
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
            await context.bot.send_message(chat_id=user_id, text=f"📬 <b>Ответ на вопрос #{ticket_num}</b>\n\n👤 @{admin_name}\n📝 {update.message.text}", parse_mode=ParseMode.HTML)
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
            await context.bot.send_message(chat_id=user_id, text=f"🎉 <b>Идея #{ticket_num} ОДОБРЕНА!</b>", parse_mode=ParseMode.HTML)
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
            await context.bot.send_message(chat_id=user_id, text=f"📋 <b>Идея #{ticket_num} отклонена.</b>", parse_mode=ParseMode.HTML)
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
    
    # Команды
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("pending", cmd_pending))
    application.add_handler(CommandHandler("stopchat", stop_chat_command))
    
    # КАТАЛОГ — ПРЯМАЯ ПОКУПКА
    application.add_handler(CallbackQueryHandler(buy_product, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(check_payment_callback, pattern="^paid_"))
    application.add_handler(CallbackQueryHandler(show_category, pattern="^cat_"))
    application.add_handler(CallbackQueryHandler(back_to_catalog, pattern="^back_to_catalog$"))
    
    application.add_handler(MessageHandler(filters.Regex("^🛍️ Каталог$"), show_catalog))
    
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
    
    # Чат-сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message))
    
    # Напоминания
    if application.job_queue:
        application.job_queue.run_repeating(check_pending_tickets, interval=3600, first=10)
    
    print("✅ Бот запущен с каталогом и прямой оплатой!")
    application.run_polling()
