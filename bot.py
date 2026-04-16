#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
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

# ========== ИМПОРТ БАЗЫ ДАННЫХ ==========
from database import init_db, save_ticket, update_ticket_status, get_user_by_message, get_ticket_status, get_old_pending_tickets

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== СОСТОЯНИЯ ==========
WAITING_IDEA, WAITING_QUESTION, WAITING_REPLY = range(3)

# Активные чаты
active_chats = {}

ticket_counter = 0

def get_next_ticket_number():
    global ticket_counter
    ticket_counter += 1
    return ticket_counter

# ========== ПРАЙС-ЛИСТ ==========
PRICE_LIST_IMAGE = "https://fotora.ru/uploaded/?ID=PCQPD16042026204837"  # Замените на свою картинку

PRICE_LIST_TEXT = """
💰 <b>ПРАЙС-ЛИСТ</b>

━━━━━━━━━━━━━━━━━━━━━

 Private block - 50₽
 Private skins - 20₽
 Private models - 30₽
 Private sborka - договор

━━━━━━━━━━━━━━━━━━━━━

Нажмите кнопку «📞 Связь с администрацией» и напишите, что хотите заказать. Мы ответим в ближайшее время!

💳 <b>ОПЛАТА</b>
• Перевод на карту
• ЮMoney
• ТГ звезды

━━━━━━━━━━━━━━━━━━━━━

<i>Цены актуальны на апрель 2026 г.</i>
"""

# ========== КЛАВИАТУРА ==========
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Отправить идею")],
        [KeyboardButton(text="❓ Задать вопрос")],
        [KeyboardButton(text="💰 Прайс-лист")],
        [KeyboardButton(text="📞 Связь с администрацией")]
    ],
    resize_keyboard=True
)

# ========== ПРАЙС-ЛИСТ ==========
async def show_price_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет прайс-лист с картинкой"""
    try:
        await update.message.reply_photo(
            photo=PRICE_LIST_IMAGE,
            caption=PRICE_LIST_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        logger.info(f"📋 Прайс-лист отправлен пользователю {update.effective_user.id}")
    except Exception as e:
        # Если картинка не загрузилась — отправляем только текст
        logger.error(f"Ошибка отправки фото: {e}")
        await update.message.reply_text(
            PRICE_LIST_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard,
            disable_web_page_preview=True
        )

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
    
    # Прайс-лист
    application.add_handler(MessageHandler(filters.Regex("^💰 Прайс-лист$"), show_price_list))
    
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
    
    print("✅ Бот запущен с прайс-листом!")
    application.run_polling()
