import aiosqlite
from datetime import datetime, timedelta

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

# Получить заявки в статусе pending, созданные более hours часов назад
async def get_old_pending_tickets(hours=24):
    """Возвращает список заявок в статусе 'pending', которые висят более hours часов"""
    async with aiosqlite.connect(DB_NAME) as db:
        cutoff_time = (datetime.now() - timedelta(hours=hours)).isoformat()
        
        cursor = await db.execute(
            """
            SELECT id, type, content, status, created_at 
            FROM tickets 
            WHERE status = 'pending' AND created_at < ?
            ORDER BY created_at ASC
            """,
            (cutoff_time,)
        )
        
        rows = await cursor.fetchall()
        tickets = []
        for row in rows:
            tickets.append({
                'id': row[0],
                'type': row[1],
                'content': row[2],
                'status': row[3],
                'created_at': row[4]
            })
        return tickets

# Получить статистику по заявкам
async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        # Всего заявок
        cursor = await db.execute("SELECT COUNT(*) FROM tickets")
        total = (await cursor.fetchone())[0]
        
        # В ожидании
        cursor = await db.execute("SELECT COUNT(*) FROM tickets WHERE status = 'pending'")
        pending = (await cursor.fetchone())[0]
        
        # Одобрено
        cursor = await db.execute("SELECT COUNT(*) FROM tickets WHERE status = 'approved'")
        approved = (await cursor.fetchone())[0]
        
        # Отклонено
        cursor = await db.execute("SELECT COUNT(*) FROM tickets WHERE status = 'rejected'")
        rejected = (await cursor.fetchone())[0]
        
        # Отвечено
        cursor = await db.execute("SELECT COUNT(*) FROM tickets WHERE status = 'answered'")
        answered = (await cursor.fetchone())[0]
        
        return {
            'total': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'answered': answered
        }

# Получить все заявки (с фильтром по статусу)
async def get_all_tickets(status_filter=None):
    async with aiosqlite.connect(DB_NAME) as db:
        if status_filter and status_filter != 'all':
            cursor = await db.execute(
                "SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC",
                (status_filter,)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM tickets ORDER BY created_at DESC"
            )
        
        rows = await cursor.fetchall()
        tickets = []
        for row in rows:
            tickets.append({
                'id': row[0],
                'user_id': row[1],
                'user_username': row[2],
                'message_id': row[3],
                'type': row[4],
                'content': row[5],
                'status': row[6],
                'created_at': row[7]
            })
        return tickets

# Получить заявку по ID
async def get_ticket_by_id(ticket_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE id = ?",
            (ticket_id,)
        )
        row = await cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'user_id': row[1],
                'user_username': row[2],
                'message_id': row[3],
                'type': row[4],
                'content': row[5],
                'status': row[6],
                'created_at': row[7]
            }
        return None

# Обновить статус заявки по ID
async def update_ticket_status_by_id(ticket_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE tickets SET status = ? WHERE id = ?",
            (status, ticket_id)
        )
        await db.commit()

# Поиск заявок по ключевому слову
async def search_tickets(query):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT id, type, content, status, created_at 
            FROM tickets 
            WHERE content LIKE ? OR user_username LIKE ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (f'%{query}%', f'%{query}%')
        )
        rows = await cursor.fetchall()
        tickets = []
        for row in rows:
            tickets.append({
                'id': row[0],
                'type': row[1],
                'content': row[2],
                'status': row[3],
                'created_at': row[4]
            })
        return tickets
