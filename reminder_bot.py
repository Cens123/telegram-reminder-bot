import logging
import sqlite3
import datetime
import asyncio
import json
import threading # Импортируем threading для запуска Flask в отдельном потоке

from flask import Flask # Импортируем Flask
from apscheduler.schedulers.blocking import BlockingScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# --- Настройки логгирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Настройки Flask для поддержания активности ---
app = Flask(__name__)

@app.route('/')
def home():
    """Простая домашняя страница для Flask-сервера."""
    return "Bot is running!"

def run_flask():
    """Запускает Flask-сервер в отдельном потоке."""
    # Replit автоматически предоставляет порт через переменную окружения PORT.
    # Если ее нет, используем 8080 по умолчанию.
    port = 8080 #int(os.environ.get('PORT', 8080)) # Можно использовать os.environ.get('PORT')
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    """Запускает Flask-сервер в фоновом потоке."""
    t = threading.Thread(target=run_flask)
    t.daemon = True # Позволяет потоку завершаться, когда завершается основная программа
    t.start()

# --- Получение токена: ТОЛЬКО из кода (небезопасно для публичных репозиториев!) ---
TOKEN = '8031651136:AAHyIOOfWUmny-p2Lz3072cxY3yhL5LNL0o' # <-- Вставьте ваш токен Telegram сюда!

if not TOKEN or TOKEN == 'ВАШ_ТОКЕН_БОТА_ЗДЕСЬ':
    raise ValueError("Токен Telegram не установлен. Вставьте его в строку TOKEN = '...'")

# --- Функции для работы с базой данных ---
DB_NAME = 'reminders.db'

def init_db():
    """Инициализирует базу данных SQLite."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                reminder_time TEXT NOT NULL,
                is_sent INTEGER DEFAULT 0,
                interval_type TEXT,
                specific_days TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("База данных успешно инициализирована или уже существует.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}", exc_info=True)

def add_reminder_to_db(chat_id, message, reminder_time, interval_type='once', specific_days=None):
    """Добавляет напоминание в базу данных."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (chat_id, message, reminder_time, interval_type, specific_days) VALUES (?, ?, ?, ?, ?)",
            (chat_id, message, reminder_time, interval_type, specific_days)
        )
        conn.commit()
        conn.close()
        logger.info(f"Напоминание '{message}' для {chat_id} на {reminder_time} добавлено.")
        return cursor.lastrowid
    except Exception as e:
        logger.error(f"Ошибка при добавлении напоминания в БД: {e}", exc_info=True)
        return None

def get_reminders_from_db(is_sent=0):
    """Получает напоминания из базы данных."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id, message, reminder_time, interval_type, specific_days FROM reminders WHERE is_sent = ?", (is_sent,))
        reminders = cursor.fetchall()
        conn.close()
        return reminders
    except Exception as e:
        logger.error(f"Ошибка при получении напоминаний из БД: {e}", exc_info=True)
        return []

def mark_reminder_as_sent(reminder_id):
    """Помечает напоминание как отправленное."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()
        logger.info(f"Напоминание с ID {reminder_id} помечено как отправленное.")
    except Exception as e:
        logger.error(f"Ошибка при пометке напоминания как отправленного: {e}", exc_info=True)

def delete_reminder_from_db(reminder_id):
    """Удаляет напоминание из базы данных."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()
        logger.info(f"Напоминание с ID {reminder_id} удалено из БД.")
    except Exception as e:
        logger.error(f"Ошибка при удалении напоминания из БД: {e}", exc_info=True)

# --- Вспомогательные функции для клавиатуры и времени ---

def get_interval_keyboard():
    """Возвращает клавиатуру для выбора интервала напоминания."""
    keyboard = [
        [InlineKeyboardButton("Однократно", callback_data="interval_once")],
        [InlineKeyboardButton("Ежедневно", callback_data="interval_daily")],
        [InlineKeyboardButton("Еженедельно", callback_data="interval_weekly")],
        [InlineKeyboardButton("По дням недели", callback_data="interval_specific_days")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_specific_days_keyboard(selected_days=None):
    """Возвращает клавиатуру для выбора дней недели."""
    if selected_days is None:
        selected_days = []
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard = []
    row = []
    for i, day in enumerate(days):
        button_text = f"{day} ✅" if i in selected_days else day
        row.append(InlineKeyboardButton(button_text, callback_data=f"day_{i}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("Подтвердить дни", callback_data="confirm_days")])
    return InlineKeyboardMarkup(keyboard)

# --- Глобальные состояния для обработки ввода ---
user_states = {}

# --- Функции обработчиков команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и предлагает создать напоминание."""
    user_name = update.effective_user.first_name if update.effective_user else "друг"
    await update.message.reply_text(
        f"Привет, {user_name}! Я бот-напоминалка. ⏰\n\n"
        "Я помогу тебе не забывать о важных делах.\n"
        "Чтобы создать напоминание, набери /remind."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с доступными командами."""
    help_text = (
        "Вот что я могу:\n\n"
        "/remind - Создать новое напоминание.\n"
        "/myreminders - Посмотреть список твоих активных напоминаний.\n"
        "/help - Показать это сообщение помощи.\n\n"
        "Для создания напоминания я сначала спрошу тебя, что напомнить, "
        "потом когда, а затем с какой периодичностью."
    )
    await update.message.reply_text(help_text)

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает процесс создания напоминания."""
    chat_id = update.effective_chat.id
    user_states[chat_id] = {'state': 'waiting_for_message'}
    await update.message.reply_text("Что мне тебе напомнить? Например: 'Сделать зарядку'.")

async def my_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список активных напоминаний пользователя."""
    chat_id = update.effective_chat.id
    reminders = get_reminders_from_db(is_sent=0)
    user_reminders = [r for r in reminders if r[1] == chat_id]

    if not user_reminders:
        await update.message.reply_text("У тебя пока нет активных напоминаний.")
        return

    text = "Твои активные напоминания:\n\n"
    keyboard = []
    for r_id, _, message, reminder_time_str, interval_type, specific_days_str in user_reminders:
        rem_datetime = datetime.datetime.fromisoformat(reminder_time_str)
        time_display = rem_datetime.strftime("%d.%m.%Y %H:%M")

        interval_display = ""
        if interval_type == 'once':
            interval_display = "Однократно"
        elif interval_type == 'daily':
            interval_display = "Ежедневно"
        elif interval_type == 'weekly':
            interval_display = "Еженедельно"
        elif interval_type == 'specific_days':
            days_map = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
            selected_days_indices = json.loads(specific_days_str) if specific_days_str else []
            selected_days_names = [days_map[i] for i in selected_days_indices if i in days_map]
            interval_display = f"По дням: {', '.join(selected_days_names)}"
            
        text += f"*{message}* ({interval_display}) в *{time_display}*\n"
        keyboard.append([InlineKeyboardButton(f"Удалить: {message[:20]}...", callback_data=f"delete_{r_id}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовый ввод пользователя в зависимости от состояния."""
    chat_id = update.effective_chat.id
    user_state = user_states.get(chat_id)

    if user_state and user_state['state'] == 'waiting_for_message':
        user_states[chat_id]['message'] = update.message.text
        user_states[chat_id]['state'] = 'waiting_for_time'
        await update.message.reply_text(
            f"Отлично! '{update.message.text}'.\n\n"
            "Теперь укажи время напоминания в формате *ДД.ММ.ГГГГ ЧЧ:ММ*.\n"
            "Например: '25.12.2024 18:30'.",
            parse_mode='Markdown'
        )
    elif user_state and user_state['state'] == 'waiting_for_time':
        try:
            reminder_time = datetime.datetime.strptime(update.message.text, "%d.%m.%Y %H:%M")

            if reminder_time < datetime.datetime.now():
                await update.message.reply_text("Время напоминания не может быть в прошлом. Пожалуйста, введи корректное время.")
                return

            user_states[chat_id]['reminder_time'] = reminder_time.isoformat()
            user_states[chat_id]['state'] = 'waiting_for_interval'
            await update.message.reply_text(
                "Как часто напоминать?",
                reply_markup=get_interval_keyboard()
            )
        except ValueError:
            await update.message.reply_text(
                "Неверный формат времени. Пожалуйста, используй формат ДД.ММ.ГГГГ ЧЧ:ММ."
            )
    else:
        await update.message.reply_text("Я тебя не понимаю. Используй /remind, чтобы начать создание напоминания.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия кнопок на клавиатурах."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_state = user_states.get(chat_id)

    if query.data.startswith("interval_"):
        interval_type = query.data.split('_')[1]
        user_states[chat_id]['interval_type'] = interval_type

        if interval_type == 'specific_days':
            user_states[chat_id]['state'] = 'waiting_for_specific_days'
            user_states[chat_id]['specific_days'] = []
            await query.edit_message_text(
                text="Выбери дни недели:",
                reply_markup=get_specific_days_keyboard()
            )
        else:
            message_text = user_states[chat_id]['message']
            reminder_time_iso = user_states[chat_id]['reminder_time']
            
            add_reminder_to_db(chat_id, message_text, reminder_time_iso, interval_type)
            del user_states[chat_id]
            await query.edit_message_text(f"Напоминание '{message_text}' успешно создано!")

    elif query.data.startswith("day_"):
        if user_state and user_state['state'] == 'waiting_for_specific_days':
            day_index = int(query.data.split('_')[1])
            if day_index in user_state['specific_days']:
                user_state['specific_days'].remove(day_index)
            else:
                user_state['specific_days'].append(day_index)
            user_state['specific_days'].sort()

            await query.edit_message_reply_markup(
                reply_markup=get_specific_days_keyboard(user_state['specific_days'])
            )

    elif query.data == "confirm_days":
        if user_state and user_state['state'] == 'waiting_for_specific_days':
            if not user_state['specific_days']:
                await query.edit_message_text("Пожалуйста, выбери хотя бы один день недели.")
                return

            message_text = user_state[chat_id]['message']
            reminder_time_iso = user_states[chat_id]['reminder_time']
            specific_days_json = json.dumps(user_states[chat_id]['specific_days'])
            
            add_reminder_to_db(chat_id, message_text, reminder_time_iso, 'specific_days', specific_days_json)
            del user_states[chat_id]
            await query.edit_message_text(f"Напоминание '{message_text}' успешно создано по выбранным дням!")

    elif query.data.startswith("delete_"):
        reminder_id = int(query.data.split('_')[1])
        delete_reminder_from_db(reminder_id)
        await query.edit_message_text("Напоминание удалено.")

# --- Функция для планировщика (APScheduler) ---
def send_scheduled_reminders(bot):
    """Проверяет напоминания в БД и отправляет их."""
    now = datetime.datetime.now().replace(second=0, microsecond=0)
    current_weekday = now.weekday()

    reminders = get_reminders_from_db(is_sent=0)

    for r_id, chat_id, message, reminder_time_str, interval_type, specific_days_str in reminders:
        try:
            rem_datetime = datetime.datetime.fromisoformat(reminder_time_str)
            rem_datetime = rem_datetime.replace(second=0, microsecond=0)

            should_send = False

            if interval_type == 'once':
                if rem_datetime == now:
                    should_send = True
            elif interval_type == 'daily':
                if rem_datetime.time() == now.time():
                    should_send = True
            elif interval_type == 'weekly':
                if rem_datetime.weekday() == current_weekday and rem_datetime.time() == now.time():
                    should_send = True
            elif interval_type == 'specific_days':
                selected_days_indices = json.loads(specific_days_str) if specific_days_str else []
                if current_weekday in selected_days_indices and rem_datetime.time() == now.time():
                    should_send = True

            if should_send:
                # Создаем асинхронную задачу для отправки сообщения,
                # так как send_scheduled_reminders вызывается из синхронного потока.
                asyncio.create_task(bot.send_message(chat_id=chat_id, text=f"⏰ Напоминание: {message}"))
                logger.info(f"Отправлено напоминание с ID {r_id} для chat_id {chat_id}.")
                if interval_type == 'once':
                    mark_reminder_as_sent(r_id)

        except Exception as e:
            logger.error(f"Ошибка при обработке напоминания ID {r_id}: {e}", exc_info=True)

# --- Главная функция бота ---
def main() -> None:
    """Запускает бота."""
    logging.info("Запуск бота...")

    init_db()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("remind", remind))
    application.add_handler(CommandHandler("myreminders", my_reminders))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_input))

    # --- Инициализация и запуск планировщика в отдельном потоке ---
    scheduler = BlockingScheduler()
    scheduler.add_job(
        send_scheduled_reminders,
        'interval',
        seconds=60,
        args=(application.bot,)
    )
    
    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=scheduler.start)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    logging.info("Бот запущен и ожидает обновлений...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    # Запускаем Flask-сервер для поддержания активности
    keep_alive() # <--- Эта строка запускает Flask-сервер

    try:
        main()
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при запуске бота: {e}", exc_info=True)
