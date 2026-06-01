#!/usr/bin/env python3
"""
OxatovAccount API Server v2.0
Исправленная версия для Telegram Mini App
"""

import asyncio
import json
import random
import hashlib
import sqlite3
import re
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from aiohttp import web, ClientSession
import aiohttp

# ==================== НАСТРОЙКА ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('oxatov_api')

# ==================== КОНФИГ ====================
class Config:
    PORT = int(os.getenv('PORT', 3000))
    CODE_TIMEOUT_MINUTES = 3
    ADMIN_IDS = {123456789}  # ID администраторов
    
    # LolzTeam API (если нужно)
    LOLZTEAM_TOKEN = os.getenv('LOLZTEAM_TOKEN', 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzUxMiJ9.eyJzdWIiOjk5MzQ3MTcsImlzcyI6Imx6dCIsImlhdCI6MTc4MDMwNzY0MSwianRpIjoiOTgxNDU2Iiwic2NvcGUiOiJiYXNpYyByZWFkIHBvc3QgY29udmVyc2F0ZSBwYXltZW50IGludm9pY2UgY2hhdGJveCBtYXJrZXQiLCJleHAiOjE5Mzc5ODc2NDF9.puCcnpkc77IhuwSTH_N3GG2EC06N8qHfwzDJf2f_NkIRbs7tRKfpAIi2xlyjv0bDg5xtI5z2Y_s1uM69Cmar23qSzwb-CeWUVCfjShZCZMmOkASW_8X4-liA3eQwevONQa6pe5RU2zXtJx_QHKcZgP8WGpn6cUsDP3Uwn0MSdO4')
    LOLZTEAM_API_URL = "https://api.zelenka.guru"
    
    # Telegram Bot Token для верификации WebApp данных
    BOT_TOKEN = os.getenv('BOT_TOKEN', '8598355142:AAEDqXToQ_yHHTGW_deWFsjfAEzz3Vemdzw')  # Вставь свой токен


# ==================== БАЗА ДАННЫХ ====================
class Database:
    def __init__(self, db_path: str = "shop.db"):
        self.db_path = db_path
        self._init_db()
    
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        with self._get_connection() as conn:
            # Пользователи
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER DEFAULT 0,
                    username TEXT,
                    first_name TEXT,
                    total_bought INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Аккаунты
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_string TEXT,
                    phone TEXT,
                    country TEXT,
                    dc TEXT DEFAULT 'DC1',
                    price INTEGER NOT NULL,
                    status TEXT DEFAULT 'available',
                    buyer_id INTEGER,
                    sold_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Заказы
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER,
                    account_id INTEGER,
                    phone TEXT,
                    amount INTEGER,
                    status TEXT DEFAULT 'completed',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Коды авторизации
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    user_id INTEGER,
                    phone TEXT,
                    code TEXT,
                    expires_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Логи
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    action TEXT,
                    target TEXT,
                    details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            logger.info("База данных инициализирована")
    
    # ========== ПОЛЬЗОВАТЕЛИ ==========
    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None) -> Dict:
        with self._get_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                conn.execute(
                    "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                    (user_id, username, first_name)
                )
                conn.commit()
                user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(user)
    
    def get_balance(self, user_id: int) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return row['balance'] if row else 0
    
    def add_balance(self, user_id: int, amount: int) -> bool:
        with self._get_connection() as conn:
            conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            conn.commit()
            return True
    
    # ========== АККАУНТЫ ==========
    def get_available_accounts(self, country: str = None, limit: int = 100) -> List[Dict]:
        with self._get_connection() as conn:
            if country:
                rows = conn.execute(
                    "SELECT id, phone, country, dc, price, status FROM accounts WHERE status = 'available' AND country = ? ORDER BY price LIMIT ?",
                    (country, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, phone, country, dc, price, status FROM accounts WHERE status = 'available' ORDER BY price LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(row) for row in rows]
    
    def get_account_by_id(self, account_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            return dict(row) if row else None
    
    def buy_account(self, account_id: int, user_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            account = conn.execute(
                "SELECT * FROM accounts WHERE id = ? AND status = 'available'", 
                (account_id,)
            ).fetchone()
            
            if not account:
                return None
            
            balance = self.get_balance(user_id)
            if balance < account['price']:
                return None
            
            try:
                conn.execute("BEGIN TRANSACTION")
                
                conn.execute(
                    "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                    (account['price'], user_id)
                )
                
                conn.execute(
                    "UPDATE accounts SET status = 'sold', buyer_id = ?, sold_at = ? WHERE id = ?",
                    (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), account_id)
                )
                
                conn.execute(
                    "UPDATE users SET total_bought = total_bought + 1, total_spent = total_spent + ? WHERE user_id = ?",
                    (account['price'], user_id)
                )
                
                order_id = hashlib.md5(f"{user_id}{account_id}{datetime.now()}".encode()).hexdigest()[:16]
                code = str(random.randint(10000, 99999))
                expires = (datetime.now() + timedelta(minutes=Config.CODE_TIMEOUT_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                
                conn.execute(
                    "INSERT INTO orders (order_id, user_id, account_id, phone, amount) VALUES (?, ?, ?, ?, ?)",
                    (order_id, user_id, account_id, account['phone'], account['price'])
                )
                conn.execute(
                    "INSERT INTO auth_codes (order_id, user_id, phone, code, expires_at) VALUES (?, ?, ?, ?, ?)",
                    (order_id, user_id, account['phone'], code, expires)
                )
                
                conn.execute("COMMIT")
                logger.info(f"Покупка: user={user_id}, account={account_id}, price={account['price']}")
                
                return {
                    'order_id': order_id,
                    'code': code,
                    'phone': account['phone'],
                    'price': account['price']
                }
            except Exception as e:
                conn.execute("ROLLBACK")
                logger.error(f"Ошибка покупки: {e}")
                return None
    
    def add_account(self, session_string: str, phone: str, country: str, price: int) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO accounts (session_string, phone, country, price) 
                   VALUES (?, ?, ?, ?)""",
                (session_string, phone, country, price)
            )
            conn.commit()
            return cursor.lastrowid
    
    # ========== ЗАКАЗЫ ==========
    def get_user_orders(self, user_id: int, limit: int = 50) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def get_active_codes(self, user_id: int) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM auth_codes 
                   WHERE user_id = ? AND expires_at > datetime('now')
                   ORDER BY created_at DESC""",
                (user_id,)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def refresh_code(self, order_id: str, user_id: int, phone: str) -> Optional[str]:
        with self._get_connection() as conn:
            new_code = str(random.randint(10000, 99999))
            expires = (datetime.now() + timedelta(minutes=Config.CODE_TIMEOUT_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO auth_codes (order_id, user_id, phone, code, expires_at) VALUES (?, ?, ?, ?, ?)",
                (order_id, user_id, phone, new_code, expires)
            )
            conn.commit()
            return new_code
    
    def get_stats(self) -> Dict:
        with self._get_connection() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            revenue = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM orders").fetchone()[0]
            accounts_available = conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'available'").fetchone()[0]
            accounts_total = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            
            return {
                'users': users,
                'revenue': revenue,
                'accounts_available': accounts_available,
                'accounts_total': accounts_total
            }


db = Database()


# ==================== ВЕРИФИКАЦИЯ WEBAPP ====================
def verify_telegram_auth(init_data: str, bot_token: str) -> Optional[Dict]:
    """Проверка подписи данных от Telegram WebApp"""
    try:
        import hmac
        import hashlib
        
        params = dict(x.split('=') for x in init_data.split('&') if x)
        received_hash = params.pop('hash', None)
        
        if not received_hash:
            return None
        
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if computed_hash == received_hash:
            user_data = json.loads(params.get('user', '{}'))
            return user_data
        
        return None
    except Exception as e:
        logger.error(f"Auth verification error: {e}")
        return None


# ==================== API ХЕНДЛЕРЫ ====================
async def handle_auth(request: web.Request) -> web.Response:
    """Аутентификация через WebApp данные"""
    try:
        data = await request.json()
        init_data = data.get('initData', '')
        
        if not Config.BOT_TOKEN:
            # Если токен не настроен, используем данные "как есть"
            try:
                params = dict(x.split('=') for x in init_data.split('&') if x)
                user_data = json.loads(params.get('user', '{"id": 0, "first_name": "User"}'))
            except:
                user_data = {"id": 12345, "first_name": "User"}
        else:
            user_data = verify_telegram_auth(init_data, Config.BOT_TOKEN)
            if not user_data:
                return web.json_response({"error": "Invalid auth"}, status=401)
        
        user_id = user_data.get('id')
        username = user_data.get('username')
        first_name = user_data.get('first_name')
        
        user = db.get_or_create_user(user_id, username, first_name)
        balance = db.get_balance(user_id)
        
        return web.json_response({
            "success": True,
            "user_id": user_id,
            "balance": balance,
            "user": user_data
        })
    except Exception as e:
        logger.error(f"Auth error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_balance(request: web.Request) -> web.Response:
    user_id = request.query.get('user_id')
    if not user_id:
        return web.json_response({"error": "user_id required"}, status=400)
    
    try:
        user_id = int(user_id)
        balance = db.get_balance(user_id)
        return web.json_response({"balance": balance})
    except ValueError:
        return web.json_response({"error": "Invalid user_id"}, status=400)


async def handle_catalog(request: web.Request) -> web.Response:
    country = request.query.get('country')
    accounts = db.get_available_accounts(country)
    
    # Скрываем реальные телефоны
    for acc in accounts:
        if acc['phone'] and len(acc['phone']) > 6:
            acc['phone'] = acc['phone'][:4] + "••••" + acc['phone'][-2:]
    
    return web.json_response({
        "items": accounts,
        "total": len(accounts)
    })


async def handle_orders(request: web.Request) -> web.Response:
    user_id = request.query.get('user_id')
    if not user_id:
        return web.json_response({"error": "user_id required"}, status=400)
    
    try:
        user_id = int(user_id)
        orders = db.get_user_orders(user_id)
        return web.json_response({"orders": orders})
    except ValueError:
        return web.json_response({"error": "Invalid user_id"}, status=400)


async def handle_codes(request: web.Request) -> web.Response:
    user_id = request.query.get('user_id')
    if not user_id:
        return web.json_response({"error": "user_id required"}, status=400)
    
    try:
        user_id = int(user_id)
        codes = db.get_active_codes(user_id)
        return web.json_response({"codes": codes})
    except ValueError:
        return web.json_response({"error": "Invalid user_id"}, status=400)


async def handle_buy(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        user_id = int(data.get('user_id', 0))
        account_id = int(data.get('account_id', 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    
    if not user_id or not account_id:
        return web.json_response({"error": "user_id and account_id required"}, status=400)
    
    result = db.buy_account(account_id, user_id)
    if result:
        return web.json_response({
            "success": True,
            "order_id": result['order_id'],
            "code": result['code']
        })
    else:
        return web.json_response({
            "success": False,
            "error": "sold_out_or_insufficient_funds"
        })


async def handle_refresh_code(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        user_id = int(data.get('user_id', 0))
        order_id = data.get('order_id')
        phone = data.get('phone', '')
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid request"}, status=400)
    
    if not order_id:
        return web.json_response({"error": "order_id required"}, status=400)
    
    new_code = db.refresh_code(order_id, user_id, phone)
    if new_code:
        return web.json_response({"success": True, "code": new_code})
    return web.json_response({"success": False, "error": "order_not_found"})


async def handle_deposit(request: web.Request) -> web.Response:
    """Пополнение баланса через Telegram Stars"""
    try:
        data = await request.json()
        user_id = int(data.get('user_id', 0))
        amount = int(data.get('amount', 0))
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid request"}, status=400)
    
    if amount < 1 or amount > 10000:
        return web.json_response({"error": "Invalid amount"}, status=400)
    
    # Здесь будет интеграция с Telegram Stars Invoice
    # Пока возвращаем заглушку
    
    logger.info(f"Deposit requested: user={user_id}, amount={amount}")
    
    return web.json_response({
        "success": True,
        "invoice_link": f"https://t.me/your_bot?start=deposit_{user_id}_{amount}",
        "message": f"Для пополнения на {amount}★ перейдите по ссылке"
    })


async def handle_stats(request: web.Request) -> web.Response:
    """Публичная статистика"""
    stats = db.get_stats()
    return web.json_response(stats)


async def handle_admin_add_account(request: web.Request) -> web.Response:
    """Админский эндпоинт для добавления аккаунтов"""
    try:
        data = await request.json()
        admin_id = int(data.get('admin_id', 0))
        
        if admin_id not in Config.ADMIN_IDS:
            return web.json_response({"error": "Unauthorized"}, status=403)
        
        session_string = data.get('session_string')
        phone = data.get('phone')
        country = data.get('country', 'US')
        price = int(data.get('price', 100))
        
        if not session_string or not phone:
            return web.json_response({"error": "session_string and phone required"}, status=400)
        
        account_id = db.add_account(session_string, phone, country, price)
        
        return web.json_response({
            "success": True,
            "account_id": account_id
        })
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "Invalid request"}, status=400)


# ==================== ЗАПУСК СЕРВЕРА ====================
async def init_app() -> web.Application:
    app = web.Application()
    
    # CORS middleware
    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        if request.method == 'OPTIONS':
            response = web.Response()
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return response
        
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    
    app.middlewares.append(cors_middleware)
    
    # Роуты
    app.router.add_post('/api/auth', handle_auth)
    app.router.add_get('/api/balance', handle_balance)
    app.router.add_get('/api/catalog', handle_catalog)
    app.router.add_get('/api/orders', handle_orders)
    app.router.add_get('/api/codes', handle_codes)
    app.router.add_post('/api/buy', handle_buy)
    app.router.add_post('/api/refresh', handle_refresh_code)
    app.router.add_post('/api/deposit', handle_deposit)
    app.router.add_get('/api/stats', handle_stats)
    app.router.add_post('/api/admin/account', handle_admin_add_account)
    
    # Корень
    async def root(request):
        return web.json_response({
            "service": "OxatovAccount API",
            "version": "2.1",
            "status": "online"
        })
    app.router.add_get('/', root)
    
    return app


async def main():
    app = await init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    
    print("=" * 50)
    print("OxatovAccount API v2.1")
    print("=" * 50)
    print(f"Порт: {Config.PORT}")
    print(f"Admin ID: {Config.ADMIN_IDS}")
    print("=" * 50)
    print("GET  /api/balance?user_id=X")
    print("GET  /api/catalog")
    print("GET  /api/orders?user_id=X")
    print("GET  /api/codes?user_id=X")
    print("POST /api/buy")
    print("POST /api/refresh")
    print("POST /api/deposit")
    print("POST /api/auth")
    print("=" * 50)
    
    logger.info(f"Сервер запущен на порту {Config.PORT}")
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())