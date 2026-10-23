import asyncio
import csv
import io
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, FSInputFile,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
MANAGER_URL = os.getenv("MANAGER_URL", "https://t.me/kindbulat")

if not BOT_TOKEN: raise SystemExit("BOT_TOKEN не задан в .env")
if not ADMIN_IDS: raise SystemExit("ADMIN_IDS не задан в .env")

logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect("users.db")
conn.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    age TEXT,
    rko_knowledge TEXT,
    desired_income TEXT,
    location TEXT,
    call_ready TEXT,
    joined_at TEXT,
    last_reminded TEXT,
    subscribed INTEGER DEFAULT 1,
    blocked INTEGER DEFAULT 0
)
""")
conn.commit()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

class Quiz(StatesGroup):
    age = State()
    rko_knowledge = State()
    desired_income = State()
    location = State()
    call_ready = State()

class AdminState(StatesGroup):
    broadcast_text = State()

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    row = conn.execute("SELECT user_id FROM users WHERE user_id=?", (msg.from_user.id,)).fetchone()
    if row:
        await msg.answer("Ты уже проходил опрос. Если хочешь пообщаться с менеджером, пиши ему: " + MANAGER_URL)
        return
    await msg.answer("1/5. Сколько тебе лет?")
    await state.set_state(Quiz.age)

@dp.message(Quiz.age)
async def quiz_age(msg: Message, state: FSMContext):
    await state.update_data(age=msg.text.strip()[:50])
    await msg.answer("2/5. Насколько ты знаешь о такой вертикали как РКО? (от 0 до 10)")
    await state.set_state(Quiz.rko_knowledge)

@dp.message(Quiz.rko_knowledge)
async def quiz_rko(msg: Message, state: FSMContext):
    await state.update_data(rko=msg.text.strip()[:50])
    await msg.answer("3/5. Расскажи, на какой уровень дохода ты бы хотел сейчас выйти.")
    await state.set_state(Quiz.desired_income)

@dp.message(Quiz.desired_income)
async def quiz_income(msg: Message, state: FSMContext):
    await state.update_data(income=msg.text.strip()[:100])
    await msg.answer("4/5. Из какой ты страны и города?")
    await state.set_state(Quiz.location)

@dp.message(Quiz.location)
async def quiz_location(msg: Message, state: FSMContext):
    await state.update_data(location=msg.text.strip()[:100])
    await msg.answer("5/5. Хочешь ли ты выйти на созвон, где мы обсудим конкретно ТВОЮ ситуацию?")
    await state.set_state(Quiz.call_ready)

@dp.message(Quiz.call_ready)
async def quiz_call(msg: Message, state: FSMContext):
    data = await state.get_data()
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, username, age, rko_knowledge, desired_income, location, call_ready, joined_at) VALUES (?,?,?,?,?,?,?,?)",
        (msg.from_user.id, msg.from_user.username, data.get("age"), data.get("rko"), 
         data.get("income"), data.get("location"), msg.text.strip()[:50], datetime.now().isoformat())
    )
    conn.commit()
    await state.clear()

    text = (
        "Спасибо за ответы. Твой гайд по первым 100к уже готов. Забирай 👇\n\n"
        "Хочешь получить деньги за простые действия в РКО?\n\n"
        "Напиши мне слово «РКО» 👇\n"
        f"{MANAGER_URL}\n\n"
        "Заявки обрабатываю сразу."
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]
    ])

    pdf_path = "guide.pdf"
    if os.path.exists(pdf_path):
        file = FSInputFile(pdf_path, filename="guide.pdf")
        await msg.answer_document(file, caption=text, reply_markup=kb)
    else:
        await msg.answer(f"⚠️ (Файл guide.pdf не найден в папке).\n\n{text}", reply_markup=kb, disable_web_page_preview=True)

async def reminder_loop():
    while True:
        try:
            now = datetime.now()
            rows = conn.execute("SELECT user_id, last_reminded FROM users WHERE subscribed=1 AND blocked=0").fetchall()
            for user_id, last in rows:
                if last is None or (now - datetime.fromisoformat(last)) >= timedelta(days=2):
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]
                    ])
                    try:
                        await bot.send_message(user_id, "👋 Напоминаю: ты проходил опрос по РКО. Хочешь узнать подробнее? Жми кнопку!", reply_markup=kb)
                        conn.execute("UPDATE users SET last_reminded=? WHERE user_id=?", (now.isoformat(), user_id))
                    except Exception:
                        conn.execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))
            conn.commit()
        except Exception:
            pass
        await asyncio.sleep(3600)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Анкеты", callback_data="a_users:0")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="a_broadcast")],
        [InlineKeyboardButton(text="📤 Выгрузить CSV", callback_data="a_export")],
    ])

@dp.message(Command("admin"))
async def admin_cmd(msg: Message):
    if is_admin(msg.from_user.id):
        await msg.answer("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data == "a_stats")
async def a_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    ready = conn.execute("SELECT COUNT(*) FROM users WHERE call_ready LIKE '%да%' OR call_ready LIKE '%Да%'").fetchone()[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="a_back")]])
    await cb.message.answer(f"📊 Всего анкет: {total}\n🔥 Готовы к созвону: {ready}", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "a_back")
async def a_back(cb: CallbackQuery):
    await cb.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu())

@dp.callback_query(F.data.startswith("a_users:"))
async def a_users(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    page = int(cb.data.split(":")[1])
    rows = conn.execute("SELECT user_id, age, rko_knowledge, desired_income, location, call_ready FROM users ORDER BY joined_at DESC LIMIT 5 OFFSET ?", (page*5,)).fetchall()
    
    if not rows: return await cb.answer("Пусто", show_alert=True)
    
    text = f"👥 Анкеты (стр. {page+1})\n\n"
    for r in rows:
        text += f"ID: {r[0]} | {r[1]} лет | {r[4]}\nДоход: {r[3]} | РКО: {r[2]}/10\nСозвон: {r[5]}\n\n"
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"a_users:{page-1}"))
    nav.append(InlineKeyboardButton(text="◀️ Меню", callback_data="a_back"))
    nav.append(InlineKeyboardButton(text="➡️", callback_data=f"a_users:{page+1}"))
    
    kb = InlineKeyboardMarkup(inline_keyboard=[nav])
    try: await cb.message.edit_text(text, reply_markup=kb)
    except: await cb.message.answer(text, reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "a_broadcast")
async def a_broadcast(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    await cb.message.answer("📢 Введи текст для рассылки (или /cancel):")
    await state.set_state(AdminState.broadcast_text)
    await cb.answer()

@dp.message(AdminState.broadcast_text)
async def a_broadcast_send(msg: Message, state: FSMContext):
    if msg.text == "/cancel":
        await state.clear()
        return await msg.answer("Отменено.")
    
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать «РКО»", url=MANAGER_URL)]])
    rows = conn.execute("SELECT user_id FROM users WHERE subscribed=1 AND blocked=0").fetchall()
    sent, failed = 0, 0
    for (uid,) in rows:
        try:
            await bot.send_message(uid, msg.text, reply_markup=kb)
            sent += 1
        except Exception:
            failed += 1
            conn.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
        await asyncio.sleep(0.05)
    await msg.answer(f"✅ Отправлено: {sent}\n❌ Ошибок: {failed}", reply_markup=admin_menu())

@dp.callback_query(F.data == "a_export")
async def a_export(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return await cb.answer("Нет доступа", show_alert=True)
    rows = conn.execute("SELECT * FROM users").fetchall()
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["user_id", "username", "age", "rko_knowledge", "desired_income", "location", "call_ready", "joined_at"])
    writer.writerows(rows)
    file = BufferedInputFile(output.getvalue().encode("utf-8"), filename="leads.csv")
    await cb.message.answer_document(file, caption=f"Выгрузка: {len(rows)} анкет")
    await cb.answer()

async def main():
    asyncio.create_task(reminder_loop())
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
