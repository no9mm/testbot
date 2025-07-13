import re
import os
import asyncio
import aiohttp
import aiosqlite
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.utils import executor
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.filters import Text
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from datetime import datetime

# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
os.makedirs("logs1337", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Отдельный лог для /start
start_logger = logging.getLogger("start_logger")
start_handler = logging.FileHandler("logs1337/start.log", encoding="utf-8")
start_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
start_logger.addHandler(start_handler)
start_logger.setLevel(logging.INFO)

# === НАСТРОЙКИ ===
API_TOKEN = '7754906166:AAHt1j3h7fUINkLMuC4uyr9RtC0TNd9IWpw'
ADMIN_ID = 7129761151
DB_PATH = "users.db"

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# === FSM ДЛЯ РАССЫЛКИ ===
class Broadcast(StatesGroup):
    waiting_for_text = State()

# === FSM ДЛЯ УПРАВЛЕНИЯ АДМИНАМИ ===
class AdminManage(StatesGroup):
    waiting_for_username = State()

# === БАЗА ДАННЫХ ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                joined_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        """)
        await db.commit()
    logger.info("База данных инициализирована")

async def save_user(user: types.User):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (id, username, first_name, last_name, language_code, joined_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.username,
            user.first_name,
            user.last_name,
            user.language_code,
            datetime.now().isoformat()
        ))
        await db.commit()

async def is_main_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def is_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None

# === ПРОВЕРКА TIKTOK ССЫЛКИ ===
def is_tiktok_url(text):
    return re.search(r'(https?://)?(www\.)?(vm|vt|t)\.tiktok\.com|tiktok\.com/', text)

# === СЕРВИСЫ СКАЧИВАНИЯ ===
async def download_tikwm(url):
    try:
        async with aiohttp.ClientSession() as session:
            api = f"https://tikwm.com/api/?url={url}"
            async with session.get(api) as resp:
                data = await resp.json()
                return data["data"]["play"] if data.get("data") else None
    except Exception as e:
        logger.warning(f"tikwm ошибка: {e}")
        return None

async def download_ssstik(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://ssstik.io/abc",
                data={"id": url, "locale": "en"},
                headers={"content-type": "application/x-www-form-urlencoded"}
            ) as resp:
                html = await resp.text()
                video_url = re.search(r'href="(https://.*?\.mp4)"', html)
                return video_url.group(1) if video_url else None
    except Exception as e:
        logger.warning(f"ssstik ошибка: {e}")
        return None

async def download_tiklydown(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://tiklydown.com/getAjax?",
                data={"url": url},
                headers={"x-requested-with": "XMLHttpRequest"}
            ) as resp:
                json_data = await resp.json()
                return json_data.get("video_no_watermark")
    except Exception as e:
        logger.warning(f"tiklydown ошибка: {e}")
        return None

async def get_video_link(url):
    for downloader in [download_tikwm, download_ssstik, download_tiklydown]:
        link = await downloader(url)
        if link:
            logger.info(f"Видео получено: {link}")
            return link
    logger.error("Не удалось получить ссылку на видео")
    return None

# === КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ АДМИНАМИ ===
@dp.message_handler(commands=["addadmin"])
async def cmd_add_admin(message: Message, state: FSMContext):
    if not await is_main_admin(message.from_user.id):
        await message.reply("слыш уебок не лезь сюда ток я могу добавлять")
        return
    await message.reply("Введите username пользователя для добавления в админы (без @):")
    await state.update_data(action="add")
    await AdminManage.waiting_for_username.set()

@dp.message_handler(commands=["removeadmin"])
async def cmd_remove_admin(message: Message, state: FSMContext):
    if not await is_main_admin(message.from_user.id):
        await message.reply("ты помоему ахуел в край")
        return
    await message.reply("Введите username пользователя для удаления из админов (без @):")
    await state.update_data(action="remove")
    await AdminManage.waiting_for_username.set()

@dp.message_handler(state=AdminManage.waiting_for_username)
async def process_admin_username(message: Message, state: FSMContext):
    if not await is_main_admin(message.from_user.id):
        await message.reply("❌ Только главный админ может управлять администраторами.")
        await state.finish()
        return

    username = message.text.strip().lstrip('@')
    data = await state.get_data()
    action = data.get("action")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users WHERE username = ?", (username,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                await message.reply("❌ Пользователь с таким username не найден в базе.")
                await state.finish()
                return
            user_id = row[0]

        if action == "add":
            try:
                await db.execute("INSERT INTO admins (user_id, username) VALUES (?, ?)", (user_id, username))
                await db.commit()
                await message.reply(f"Пользователь @{username} добавлен в администраторы.")
            except aiosqlite.IntegrityError:
                await message.reply(f"Пользователь @{username} уже является администратором.")
        elif action == "remove":
            cursor = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await db.commit()
            if cursor.rowcount > 0:
                await message.reply(f"Пользователь @{username} удалён из администраторов.")
            else:
                await message.reply(f"Пользователь @{username} не найден в списке админов.")
        else:
            await message.reply("Ошибка: неизвестное действие.")
    await state.finish()

# === ОБРАБОТЧИКИ ===
@dp.message_handler(commands=['start'])
async def start_handler(message: Message):
    start_logger.info(f"/start от {message.from_user.id} @{message.from_user.username}")
    await save_user(message.from_user)
    await message.reply("Введите ссылку на Тик Ток видео")

@dp.message_handler(commands=['admin'])
async def admin_panel(message: Message):
    logger.info(f"/admin от {message.from_user.id}")
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет доступа к админ-панели.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            count = (await cursor.fetchone())[0]

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add("рассылка", "количество юзеров")
    keyboard.add("скачать базу данных", "выход")

    await message.answer(f"Админ-панель", reply_markup=keyboard)

@dp.message_handler(Text(equals="количество юзеров"))
async def show_user_count(message: Message):
    if not await is_admin(message.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            count = (await cursor.fetchone())[0]

    logger.info(f"Запрос количества пользователей от {message.from_user.id}")
    await message.answer(f"Всего пользователей: {count}")

@dp.message_handler(Text(equals="рассылка"))
async def start_broadcast(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await message.answer("Введите текст или медиа для рассылки:")
    await Broadcast.waiting_for_text.set()

@dp.message_handler(Text(equals="скачать базу данных"))
async def export_users(message: Message):
    if not await is_admin(message.from_user.id):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, username, first_name, last_name, language_code, joined_at FROM users") as cursor:
            users = await cursor.fetchall()

    file_path = "user_data.csv"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("id,username,first_name,last_name,language_code,joined_at\n")
        for user in users:
            f.write(",".join([str(i) if i is not None else "" for i in user]) + "\n")

    await message.answer_document(open(file_path, "rb"), caption="Список пользователей")

@dp.message_handler(Text(equals="выход"))
async def exit_admin(message: Message):
    if not await is_admin(message.from_user.id):
        return
    keyboard = types.ReplyKeyboardRemove()
    await message.answer("Вы вышли из админ-панели.", reply_markup=keyboard)

@dp.message_handler(state=Broadcast.waiting_for_text, content_types=types.ContentTypes.ANY)
async def send_broadcast_with_media(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return

    success, failed = 0, 0
    content_type = message.content_type
    text = message.caption if content_type in ["photo", "video", "document"] else message.text

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users") as cursor:
            users = await cursor.fetchall()

    for (user_id,) in users:
        try:
            if content_type == "text":
                await bot.send_message(user_id, text)
            elif content_type == "photo":
                await bot.send_photo(user_id, photo=message.photo[-1].file_id, caption=text)
            elif content_type == "video":
                await bot.send_video(user_id, video=message.video.file_id, caption=text)
            elif content_type == "document":
                await bot.send_document(user_id, document=message.document.file_id, caption=text)
            else:
                await bot.send_message(user_id, text or "Сообщение для рассылки")
            success += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить пользователю {user_id}: {e}")
            failed += 1

    await message.answer(f"Рассылка завершена.\nУспешно: {success}\nНеудачно: {failed}")
    await state.finish()

# === ОБРАБОТЧИК СОСЫЛОК TIKTOK ===
@dp.message_handler()
async def handle_tiktok_link(message: Message):
    if not message.text:
        return
    if is_tiktok_url(message.text):
        waiting_msg = await message.answer("Обрабатываю ссылку...")
        video_link = await get_video_link(message.text)
        if video_link:
            try:
                await bot.send_video(chat_id=message.chat.id, video=video_link, caption="coded by no9mm")
            except Exception as e:
                logger.error(f"Ошибка отправки видео: {e}")
        await waiting_msg.delete()


if __name__ == "__main__":
    async def on_startup(dp):
        await init_db()
        logger.info("Бот запущен!")

    executor.start_polling(dp, on_startup=on_startup)
