#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler, ContextTypes
from telegram.constants import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []

from database import init_db, save_ticket, update_ticket_status, get_user_by_message, get_ticket_status

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
WAITING_IDEA, WAITING_QUESTION, WAITING_REPLY = range(3)
IN_CHAT = 3

# Активные чаты
active_chats = {}

ticket_counter = 0

def get_next_ticket_number():
    global ticket_counter
    ticket_counter += 1
    return ticket_counter

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💡 Отправить идею")],
        [KeyboardButton(text="❓ Задать вопрос")],
        [KeyboardButton(text="📞 Связь с администрацией")]
    ],
    resize_keyboard=True
)

# ========== ЧАТ С АДМИНИСТРАЦИЕЙ ==========
async def request_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь нажимает кнопку связи с администрацией"""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.full_name
    
    if user_id in active_chats:
        await update.message.reply_text(
            "⏳ Вы уже находитесь в чате с администратором.\n"
            "Используйте /stopchat для завершения.",
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
                text=f"📞 <b>Запрос на связь</b>\n\n"
                     f"👤 Пользователь: @{username} (ID: <code>{user_id}</code>)\n\n"
                     f"Хочет связаться с администрацией.",
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    await update.message.reply_text(
        "✅ Ваш запрос отправлен администраторам.\n⏳ Ожидайте ответа...",
        reply_markup=main_keyboard
    )
    return ConversationHandler.END

async def accept_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ нажимает кнопку 'Принять'"""
    query = update.callback_query
    await query.answer()
    
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("⛔ Нет прав", show_alert=True)
        return
    
    user_id = int(query.data.split("_")[1])
    
    if user_id in active_chats:
        await query.edit_message_text("❌ Пользователь уже в чате с другим администратором.")
        return
    
    # Устанавливаем двустороннюю связь
    active_chats[user_id] = admin_id
    active_chats[admin_id] = user_id
    
    # Сохраняем партнёра в user_data для админа
    context.user_data["chat_partner"] = user_id
    
    await query.edit_message_text(
        f"✅ Вы приняли запрос от пользователя (ID: <code>{user_id}</code>).\n\n"
        f"💬 Теперь все ваши сообщения будут пересылаться пользователю.\n"
        f"📌 Для завершения чата отправьте /stopchat",
        parse_mode=ParseMode.HTML
    )
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="🎉 <b>Администратор подключился!</b>\n\n"
                 "💬 Теперь вы можете задать свой вопрос. Все ваши сообщения будут переданы администратору.\n"
                 "📌 Для завершения чата отправьте /stopchat",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю: {e}")
    
    # ВАЖНО: Возвращаем состояние, чтобы админ вошёл в чат
    return IN_CHAT

async def reject_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ нажимает кнопку 'Отклонить'"""
    query = update.callback_query
    await query.answer()
    
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("⛔ Нет прав", show_alert=True)
        return
    
    user_id = int(query.data.split("_")[1])
    
    await query.edit_message_text(
        f"❌ Запрос от пользователя (ID: <code>{user_id}</code>) отклонён.",
        parse_mode=ParseMode.HTML
    )
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ К сожалению, администраторы сейчас не могут ответить."
        )
    except:
        pass

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений в чате (вызывается только когда пользователь В СОСТОЯНИИ IN_CHAT)"""
    sender_id = update.effective_user.id
    
    # Проверяем, что отправитель действительно в чате
    if sender_id not in active_chats:
        await update.message.reply_text("❌ Вы не в чате.", reply_markup=main_keyboard)
        return ConversationHandler.END
    
    receiver_id = active_chats[sender_id]
    
    # Определяем префикс
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
        logger.info(f"✅ Сообщение от {sender_id} доставлено {receiver_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        await update.message.reply_text("❌ Не удалось отправить сообщение.")
    
    return IN_CHAT  # Остаёмся в чате

async def stop_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение чата по команде /stopchat"""
    user_id = update.effective_user.id
    
    if user_id not in active_chats:
        await update.message.reply_text("❌ Вы не находитесь в активном чате.", reply_markup=main_keyboard)
        return ConversationHandler.END
    
    partner_id = active_chats[user_id]
    
    del active_chats[user_id]
    del active_chats[partner_id]
    
    await update.message.reply_text("🔴 Чат завершён.", reply_markup=main_keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=partner_id,
            text="🔴 <b>Чат завершён второй стороной.</b>",
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    
    return ConversationHandler.END

# ========== КОМАНДА /start ==========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет, {update.effective_user.full_name}!\n\n"
        "Я бот для связи с командой.\nВыберите действие:",
        reply_markup=main_keyboard
    )

# ========== ОТПРАВИТЬ ИДЕЮ ==========
async def idea_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Опишите свою идею ниже:",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_IDEA

# ========== ЗАДАТЬ ВОПРОС ==========
async def question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Опишите ваш вопрос ниже:",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAITING_QUESTION

# ========== ПОЛУЧЕНИЕ ИДЕИ ==========
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
                text=(
                    f"💡 <b>НОВАЯ ИДЕЯ #{ticket_num}</b>\n\n"
                    f"👤 От: @{username} (ID: <code>{user_id}</code>)\n\n"
                    f"📄 <b>Содержание:</b>\n{message_text}"
                ),
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    await save_ticket(user_id, username, message_id, "idea", message_text)
    
    await update.message.reply_text(
        f"✅ Ваша идея отправлена на рассмотрение!\n"
        f"📌 Номер заявки: <b>#{ticket_num}</b>\n\n"
        f"Ожидайте ответа от команды.",
        reply_markup=main_keyboard,
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ========== ПОЛУЧЕНИЕ ВОПРОСА ==========
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
                text=(
                    f"❓ <b>НОВЫЙ ВОПРОС #{ticket_num}</b>\n\n"
                    f"👤 От: @{username} (ID: <code>{user_id}</code>)\n\n"
                    f"📄 <b>Вопрос:</b>\n{message_text}"
                ),
                reply_markup=admin_kb,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    await save_ticket(user_id, username, message_id, "question", message_text)
    
    await update.message.reply_text(
        f"✅ Ваш вопрос отправлен команде!\n"
        f"📌 Номер заявки: <b>#{ticket_num}</b>\n\n"
        f"Ожидайте ответа от команды.",
        reply_markup=main_keyboard,
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# ========== КНОПКА "ОТВЕТИТЬ" ==========
async def reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("⛔ Нет прав", show_alert=True)
        return ConversationHandler.END
    
    parts = query.data.split("_")
    message_id = int(parts[1])
    ticket_num = parts[2] if len(parts) > 2 else "?"
    
    status = await get_ticket_status(message_id)
    if status and status != "pending":
        await query.answer(f"⛔ Заявка #{ticket_num} уже обработана", show_alert=True)
        return ConversationHandler.END
    
    context.user_data["reply_to_msg"] = message_id
    context.user_data["ticket_num"] = ticket_num
    
    await query.message.reply_text(f"✏️ Напишите ответ пользователю (заявка #{ticket_num}):")
    return WAITING_REPLY

# ========== ОТПРАВКА ОТВЕТА ==========
async def send_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    
    original_msg_id = context.user_data.get("reply_to_msg")
    ticket_num = context.user_data.get("ticket_num", "?")
    
    if not original_msg_id:
        await update.message.reply_text("❌ Ошибка: не найден ID сообщения")
        return ConversationHandler.END
    
    status = await get_ticket_status(original_msg_id)
    if status and status != "pending":
        await update.message.reply_text(f"⛔ Заявка #{ticket_num} уже обработана.")
        return ConversationHandler.END
    
    user_id, ticket_type = await get_user_by_message(original_msg_id)
    admin_name = update.effective_user.username or update.effective_user.full_name
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"📬 <b>Ответ на ваш вопрос #{ticket_num}</b>\n\n"
                    f"👤 <b>Администратор:</b> @{admin_name}\n"
                    f"📝 <b>Ответ:</b>\n{update.message.text}"
                ),
                parse_mode=ParseMode.HTML
            )
            await update_ticket_status(original_msg_id, "answered")
            await update.message.reply_text(f"✅ Ответ на заявку #{ticket_num} отправлен!")
            
            for admin in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin,
                        text=f"✅ <b>Администратор @{admin_name}</b> ответил на вопрос <b>#{ticket_num}</b>.",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    else:
        await update.message.reply_text("❌ Пользователь не найден")
    
    return ConversationHandler.END

# ========== ОДОБРЕНИЕ ИДЕИ ==========
async def approve_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("⛔ Нет прав", show_alert=True)
        return
    
    parts = query.data.split("_")
    message_id = int(parts[1])
    ticket_num = parts[2] if len(parts) > 2 else "?"
    
    status = await get_ticket_status(message_id)
    if status and status != "pending":
        await query.answer(f"⛔ Заявка #{ticket_num} уже обработана", show_alert=True)
        return
    
    user_id, _ = await get_user_by_message(message_id)
    admin_name = query.from_user.username or query.from_user.full_name
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 <b>Отличные новости!</b>\n\n"
                    f"Ваша идея <b>#{ticket_num}</b> была <b>ОДОБРЕНА</b>!\n\n"
                    f"👤 <b>Администратор:</b> @{admin_name}\n"
                    f"Спасибо за ваш вклад!"
                ),
                parse_mode=ParseMode.HTML
            )
            await update_ticket_status(message_id, "approved")
            await query.edit_message_reply_markup(reply_markup=None)
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"🎉 <b>Администратор @{admin_name}</b> одобрил идею <b>#{ticket_num}</b>.",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        except Exception as e:
            await query.answer(f"❌ Ошибка: {e}", show_alert=True)
    else:
        await query.answer("❌ Пользователь не найден", show_alert=True)

# ========== ОТКЛОНЕНИЕ ИДЕИ ==========
async def reject_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("⛔ Нет прав", show_alert=True)
        return
    
    parts = query.data.split("_")
    message_id = int(parts[1])
    ticket_num = parts[2] if len(parts) > 2 else "?"
    
    status = await get_ticket_status(message_id)
    if status and status != "pending":
        await query.answer(f"⛔ Заявка #{ticket_num} уже обработана", show_alert=True)
        return
    
    user_id, _ = await get_user_by_message(message_id)
    admin_name = query.from_user.username or query.from_user.full_name
    
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"📋 <b>Статус вашей идеи #{ticket_num}</b>\n\n"
                    f"К сожалению, ваша идея пока не может быть реализована.\n\n"
                    f"👤 <b>Администратор:</b> @{admin_name}\n"
                    f"Но мы ценим ваше участие!"
                ),
                parse_mode=ParseMode.HTML
            )
            await update_ticket_status(message_id, "rejected")
            await query.edit_message_reply_markup(reply_markup=None)
            
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"📋 <b>Администратор @{admin_name}</b> отклонил идею <b>#{ticket_num}</b>.",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        except Exception as e:
            await query.answer(f"❌ Ошибка: {e}", show_alert=True)
    else:
        await query.answer("❌ Пользователь не найден", show_alert=True)

# ========== ОТМЕНА ==========
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено.", reply_markup=main_keyboard)
    return ConversationHandler.END

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    loop.run_until_complete(init_db())
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Чат с администрацией
    chat_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📞 Связь с администрацией$"), request_chat),
            CallbackQueryHandler(accept_chat, pattern="^accept_")
        ],
        states={
            IN_CHAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message),
                CommandHandler("stopchat", stop_chat_command)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    idea_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💡 Отправить идею$"), idea_start)],
        states={
            WAITING_IDEA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_idea)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    question_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❓ Задать вопрос$"), question_start)],
        states={
            WAITING_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_question)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reply_button, pattern="^reply_")],
        states={
            WAITING_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_reply)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("stopchat", stop_chat_command))
    application.add_handler(chat_conv)
    application.add_handler(idea_conv)
    application.add_handler(question_conv)
    application.add_handler(reply_conv)
    application.add_handler(CallbackQueryHandler(reject_chat, pattern="^reject_"))
    application.add_handler(CallbackQueryHandler(approve_button, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(reject_button, pattern="^reject_"))
    
    print("✅ Бот запущен!")
    
    application.run_polling()
