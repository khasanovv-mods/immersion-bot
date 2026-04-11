#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session
from functools import wraps
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("WEB_PANEL_SECRET", "your-secret-key-change-this")

# Пароль для входа (из переменных окружения)
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "admin123")

DB_NAME = "bot_database.db"

# ========== ДЕКОРАТОР ДЛЯ ЗАЩИТЫ СТРАНИЦ ==========
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ========== РАБОТА С БАЗОЙ ДАННЫХ ==========
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    stats = {}
    
    # Всего
    cursor.execute("SELECT COUNT(*) FROM tickets")
    stats['total'] = cursor.fetchone()[0]
    
    # По статусам
    for status in ['pending', 'approved', 'rejected', 'answered']:
        cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = ?", (status,))
        stats[status] = cursor.fetchone()[0]
    
    conn.close()
    return stats

def get_tickets(status_filter=None):
    conn = get_db()
    cursor = conn.cursor()
    
    if status_filter and status_filter != 'all':
        cursor.execute(
            "SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC",
            (status_filter,)
        )
    else:
        cursor.execute("SELECT * FROM tickets ORDER BY created_at DESC")
    
    tickets = cursor.fetchall()
    conn.close()
    return tickets

def get_ticket(ticket_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    ticket = cursor.fetchone()
    conn.close()
    return ticket

def update_ticket_status_db(ticket_id, status):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
    conn.commit()
    conn.close()

# ========== МАРШРУТЫ ==========
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['password'] == PANEL_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Неверный пароль'
    
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    stats = get_stats()
    return render_template('index.html', stats=stats)

@app.route('/tickets')
@login_required
def tickets():
    status_filter = request.args.get('status', 'all')
    all_tickets = get_tickets(status_filter)
    stats = get_stats()
    return render_template(
        'tickets.html',
        tickets=all_tickets,
        stats=stats,
        current_filter=status_filter
    )

@app.route('/ticket/<int:ticket_id>')
@login_required
def ticket_detail(ticket_id):
    ticket = get_ticket(ticket_id)
    if not ticket:
        return redirect(url_for('tickets'))
    return render_template('ticket_detail.html', ticket=ticket)

@app.route('/ticket/<int:ticket_id>/status', methods=['POST'])
@login_required
def update_status(ticket_id):
    new_status = request.form.get('status')
    if new_status in ['pending', 'approved', 'rejected', 'answered']:
        update_ticket_status_db(ticket_id, new_status)
    return redirect(url_for('ticket_detail', ticket_id=ticket_id))

# ========== ЗАПУСК ==========
def run_web_panel(host='0.0.0.0', port=5000):
    print(f"🌐 Веб-панель запущена на http://0.0.0.0:{port}")
    print(f"🔑 Пароль для входа: {PANEL_PASSWORD}")
    app.run(host=host, port=port, debug=False)

if __name__ == '__main__':
    run_web_panel()