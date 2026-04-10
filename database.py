import aiosqlite

DB_NAME = "bot_database.db"

# Создаем таблицу при первом запуске
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_username TEXT,
                message_id INTEGER,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

# Сохранить заявку в базу
async def save_ticket(user_id, username, message_id, ticket_type, content):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO tickets (user_id, user_username, message_id, type, content) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, message_id, ticket_type, content)
        )
        await db.commit()

# Обновить статус заявки
async def update_ticket_status(message_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE tickets SET status = ? WHERE message_id = ?",
            (status, message_id)
        )
        await db.commit()

# Получить ID пользователя по message_id
async def get_user_by_message(message_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id, type FROM tickets WHERE message_id = ?",
            (message_id,)
        )
        row = await cursor.fetchone()
        return row if row else (None, None)

# Получить статус заявки по message_id
async def get_ticket_status(message_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT status FROM tickets WHERE message_id = ?",
            (message_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None