import logging
import sqlite3
import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import asyncio # Для асинхронной работы с scheduler

# Включите логирование, чтобы видеть, что происходит
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Замените 'ВАШ_ТОКЕН_БОТА' на токен, который вы получили от BotFather
TOKEN = '8031651136:AAFn6zQlfNO4WBdDxACko_MlBzJ19lmocBY'
DB_NAME = 'reminders.db'

# Инициализация планировщика задач
scheduler = AsyncIOScheduler()

# --- База данных ---
def init_db():
    """Инициализирует базу данных."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            state TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            text TEXT NOT NULL,
            time TEXT NOT NULL, -- Формат HH:MM
            days TEXT NOT NULL, -- 'everyday', 'weekdays', 'specific:mon,tue,wed'
            active INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    conn.commit()
    conn.close()

def save_user_state(user_id: int, state: str):
    """Сохраняет состояние пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users (user_id, state) VALUES (?, ?)', (user_id, state))
    conn.commit()
    conn.close()

def get_user_state(user_id: int) -> str:
    """Получает состояние пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT state FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 'start'

def add_reminder_to_db(user_id: int, text: str, time: str, days: str) -> int:
    """Добавляет напоминание в базу данных и возвращает его ID."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO reminders (user_id, text, time, days) VALUES (?, ?, ?, ?)',
        (user_id, text, time, days)
    )
    reminder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def get_reminders_from_db(user_id: int) -> list:
    """Получает все активные напоминания пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT reminder_id, text, time, days FROM reminders WHERE user_id = ? AND active = 1', (user_id,))
    reminders = cursor.fetchall()
    conn.close()
    return reminders

def get_reminder_by_id(reminder_id: int) -> tuple | None:
    """Получает напоминание по его ID."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT reminder_id, user_id, text, time, days FROM reminders WHERE reminder_id = ?', (reminder_id,))
    reminder = cursor.fetchone()
    conn.close()
    return reminder

def update_reminder_in_db(reminder_id: int, text: str = None, time: str = None, days: str = None):
    """Обновляет напоминание в базе данных."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    updates = []
    params = []
    if text:
        updates.append("text = ?")
        params.append(text)
    if time:
        updates.append("time = ?")
        params.append(time)
    if days:
        updates.append("days = ?")
        params.append(days)

    if updates:
        query = f"UPDATE reminders SET {', '.join(updates)} WHERE reminder_id = ?"
        params.append(reminder_id)
        cursor.execute(query, tuple(params))
        conn.commit()
    conn.close()

def delete_reminder_from_db(reminder_id: int):
    """Удаляет напоминание из базы данных (деактивирует)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE reminders SET active = 0 WHERE reminder_id = ?', (reminder_id,))
    conn.commit()
    conn.close()

def count_active_reminders(user_id: int) -> int:
    """Считает количество активных напоминаний у пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reminders WHERE user_id = ? AND active = 1', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# --- Функции планировщика APScheduler ---
async def send_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, reminder_text: str):
    """Отправляет напоминание пользователю."""
    await context.bot.send_message(chat_id=user_id, text=f"🔔 Напоминание: {reminder_text}")
    logger.info(f"Напоминание '{reminder_text}' отправлено пользователю {user_id}")


def schedule_reminder_job(context: ContextTypes.DEFAULT_TYPE, user_id: int, reminder_id: int, text: str, time_str: str, days_str: str):
    """Планирует задачу для напоминания."""
    # Удаляем старую задачу, если она есть
    job_id = f"reminder_{user_id}_{reminder_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    hour, minute = map(int, time_str.split(':'))

    if days_str == 'everyday':
        scheduler.add_job(send_reminder, 'cron', hour=hour, minute=minute, args=[context, user_id, text], id=job_id)
        logger.info(f"Запланировано напоминание '{text}' для {user_id} ежедневно в {time_str}")
    elif days_str == 'weekdays':
        # Дни недели в APScheduler: 0=Понедельник, 1=Вторник, ..., 6=Воскресенье
        scheduler.add_job(send_reminder, 'cron', day_of_week='mon-fri', hour=hour, minute=minute, args=[context, user_id, text], id=job_id)
        logger.info(f"Запланировано напоминание '{text}' для {user_id} по будням в {time_str}")
    elif days_str.startswith('specific:'):
        specific_days_codes = {
            'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6
        }
        days_list = [specific_days_codes[d.strip()] for d in days_str[len('specific:'):].split(',')]
        scheduler.add_job(send_reminder, 'cron', day_of_week=days_list, hour=hour, minute=minute, args=[context, user_id, text], id=job_id)
        logger.info(f"Запланировано напоминание '{text}' для {user_id} по дням {days_list} в {time_str}")

# --- Обработчики команд и сообщений ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start."""
    user_id = update.effective_user.id
    init_db() # Убедимся, что база данных инициализирована
    save_user_state(user_id, 'start')

    keyboard = [
        [
            InlineKeyboardButton("Базовый пресет 🚀", callback_data="preset"),
            InlineKeyboardButton("Настроить свои напоминания ⚙️", callback_data="custom"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(
        f"Привет, {update.effective_user.mention_html()}! 👋\n"
        "Я бот для напоминаний. Помогу тебе ничего не забыть.\n\n"
        "Для начала, выбери, как ты хочешь настроить напоминания:",
        reply_markup=reply_markup,
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия на Inline кнопки."""
    query = update.callback_query
    await query.answer() # Отвечаем на callbackQuery, чтобы кнопка перестала быть "висячей"
    user_id = query.from_user.id

    if query.data == "preset":
        # Базовый пресет напоминаний
        base_reminders = [
            {"text": "Сделать зарядку", "time": "10:00", "days": "everyday"},
            {"text": "Посмотреть мотивирующий ролик", "time": "10:00", "days": "everyday"},
            {"text": "Почитать книгу", "time": "20:00", "days": "everyday"},
            {"text": "Составить планы на завтрашний день", "time": "22:00", "days": "everyday"},
        ]
        # Проверим, чтобы не добавить больше 5 напоминаний
        current_reminders_count = count_active_reminders(user_id)
        if current_reminders_count + len(base_reminders) > 5:
            await query.edit_message_text("Не могу добавить базовый пресет, у вас уже слишком много напоминаний (максимум 5). Удалите некоторые, чтобы добавить пресет.")
            return

        for r in base_reminders:
            reminder_id = add_reminder_to_db(user_id, r["text"], r["time"], r["days"])
            schedule_reminder_job(context, user_id, reminder_id, r["text"], r["time"], r["days"])

        await query.edit_message_text(
            "Базовый пресет напоминаний успешно установлен!\n"
            "Вы можете просмотреть и отредактировать их с помощью команды /myreminders."
        )
        save_user_state(user_id, 'start')

    elif query.data == "custom":
        # Настроить свои напоминания
        await query.edit_message_text("Отлично! Давайте создадим ваше первое напоминание.")
        await ask_for_reminder_text(user_id, context)

    elif query.data == "add_reminder":
        if count_active_reminders(user_id) >= 5:
            await query.edit_message_text("Вы достигли лимита в 5 напоминаний в день. Удалите некоторые, чтобы добавить новые.")
            return
        await query.edit_message_text("Хорошо, введите текст для нового напоминания:")
        save_user_state(user_id, 'awaiting_reminder_text')

    elif query.data.startswith("edit_reminder_"):
        reminder_id = int(query.data.split('_')[2])
        context.user_data['editing_reminder_id'] = reminder_id
        await query.edit_message_text("Что вы хотите отредактировать в этом напоминании?",
                                     reply_markup=InlineKeyboardMarkup([
                                         [InlineKeyboardButton("Текст", callback_data=f"edit_text_{reminder_id}")],
                                         [InlineKeyboardButton("Время", callback_data=f"edit_time_{reminder_id}")],
                                         [InlineKeyboardButton("Дни недели", callback_data=f"edit_days_{reminder_id}")],
                                         [InlineKeyboardButton("Назад к моим напоминаниям", callback_data="my_reminders")]
                                     ]))
        save_user_state(user_id, 'editing_reminder')

    elif query.data.startswith("edit_text_"):
        reminder_id = int(query.data.split('_')[2])
        context.user_data['editing_reminder_id'] = reminder_id
        await query.edit_message_text("Введите новый текст для напоминания:")
        save_user_state(user_id, 'awaiting_edit_text')

    elif query.data.startswith("edit_time_"):
        reminder_id = int(query.data.split('_')[2])
        context.user_data['editing_reminder_id'] = reminder_id
        await query.edit_message_text("Введите новое время для напоминания в формате ЧЧ:ММ (например, 14:30):")
        save_user_state(user_id, 'awaiting_edit_time')

    elif query.data.startswith("edit_days_"):
        reminder_id = int(query.data.split('_')[2])
        context.user_data['editing_reminder_id'] = reminder_id
        await query.edit_message_text("Выберите новые дни недели для напоминания:", reply_markup=get_days_keyboard())
        save_user_state(user_id, 'awaiting_edit_days')

    elif query.data.startswith("delete_reminder_"):
        reminder_id = int(query.data.split('_')[2])
        delete_reminder_from_db(reminder_id)
        # Удаляем задачу из планировщика
        job_id = f"reminder_{user_id}_{reminder_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        await query.edit_message_text("Напоминание успешно удалено.")
        await show_my_reminders(update, context) # Обновляем список напоминаний

    elif query.data == "my_reminders":
        await show_my_reminders(update, context)

    # Обработка выбора дней недели при создании/редактировании
    elif query.data in ['days_everyday', 'days_weekdays', 'days_specific']:
        context.user_data['selected_days_type'] = query.data
        if query.data == 'days_specific':
            keyboard = [
                [InlineKeyboardButton("Пн", callback_data="day_mon"),
                 InlineKeyboardButton("Вт", callback_data="day_tue"),
                 InlineKeyboardButton("Ср", callback_data="day_wed")],
                [InlineKeyboardButton("Чт", callback_data="day_thu"),
                 InlineKeyboardButton("Пт", callback_data="day_fri"),
                 InlineKeyboardButton("Сб", callback_data="day_sat")],
                [InlineKeyboardButton("Вс", callback_data="day_sun")],
                [InlineKeyboardButton("Сохранить дни", callback_data="save_specific_days")]
            ]
            await query.edit_message_text("Выберите конкретные дни недели:", reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['specific_days'] = []
        else:
            # Сохраняем выбранный тип дней
            days_str = ""
            if query.data == 'days_everyday':
                days_str = 'everyday'
            elif query.data == 'days_weekdays':
                days_str = 'weekdays'

            current_state = get_user_state(user_id)
            if current_state == 'awaiting_days':
                context.user_data['reminder_days'] = days_str
                await ask_for_reminder_time(user_id, context)
            elif current_state == 'awaiting_edit_days':
                reminder_id = context.user_data.get('editing_reminder_id')
                if reminder_id:
                    update_reminder_in_db(reminder_id, days=days_str)
                    reminder_data = get_reminder_by_id(reminder_id)
                    if reminder_data:
                        r_id, u_id, text, time, days = reminder_data
                        schedule_reminder_job(context, u_id, r_id, text, time, days)
                    await query.edit_message_text("Дни недели успешно обновлены.")
                    await show_my_reminders(update, context)
                save_user_state(user_id, 'start') # Возвращаем в начальное состояние
            else:
                 await query.edit_message_text("Произошла ошибка в логике выбора дней. Пожалуйста, попробуйте снова с /start.")

    elif query.data.startswith("day_"):
        day = query.data.split('_')[1]
        if day not in context.user_data['specific_days']:
            context.user_data['specific_days'].append(day)
        # Обновляем сообщение с выбранными днями
        selected_days_text = ", ".join([d.upper() for d in context.user_data['specific_days']])
        await query.edit_message_text(f"Выбрано: {selected_days_text}\nВыберите конкретные дни недели:", reply_markup=get_specific_days_keyboard(context.user_data['specific_days']))

    elif query.data == "save_specific_days":
        if not context.user_data.get('specific_days'):
            await query.edit_message_text("Вы не выбрали ни одного дня. Пожалуйста, выберите дни или отмените.")
            return

        specific_days_str = "specific:" + ",".join(context.user_data['specific_days'])

        current_state = get_user_state(user_id)
        if current_state == 'awaiting_days':
            context.user_data['reminder_days'] = specific_days_str
            await ask_for_reminder_time(user_id, context)
        elif current_state == 'awaiting_edit_days':
            reminder_id = context.user_data.get('editing_reminder_id')
            if reminder_id:
                update_reminder_in_db(reminder_id, days=specific_days_str)
                reminder_data = get_reminder_by_id(reminder_id)
                if reminder_data:
                    r_id, u_id, text, time, days = reminder_data
                    schedule_reminder_job(context, u_id, r_id, text, time, days)
                await query.edit_message_text("Дни недели успешно обновлены.")
                await show_my_reminders(update, context)
            save_user_state(user_id, 'start') # Возвращаем в начальное состояние
        else:
            await query.edit_message_text("Произошла ошибка в логике сохранения дней. Пожалуйста, попробуйте снова с /start.")


async def ask_for_reminder_text(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запрашивает текст напоминания."""
    keyboard = [
        [InlineKeyboardButton("Сделать зарядку", callback_data="preset_text_charge")],
        [InlineKeyboardButton("Посмотреть мотивирующий ролик", callback_data="preset_text_motiv")],
        [InlineKeyboardButton("Почитать книгу", callback_data="preset_text_book")],
        [InlineKeyboardButton("Составить планы на завтрашний день", callback_data="preset_text_plans")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=user_id,
        text="Выбери базовое напоминание или введи свой текст:",
        reply_markup=reply_markup,
    )
    save_user_state(user_id, 'awaiting_reminder_text')

async def ask_for_reminder_time(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запрашивает время напоминания."""
    await context.bot.send_message(
        chat_id=user_id,
        text="Теперь введи время для напоминания в формате ЧЧ:ММ (например, 14:30):"
    )
    save_user_state(user_id, 'awaiting_reminder_time')

async def ask_for_reminder_days(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запрашивает дни недели для напоминания."""
    await context.bot.send_message(
        chat_id=user_id,
        text="Когда должно приходить напоминание?",
        reply_markup=get_days_keyboard()
    )
    save_user_state(user_id, 'awaiting_days')

def get_days_keyboard():
    """Возвращает клавиатуру для выбора дней недели."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Каждый день", callback_data="days_everyday")],
        [InlineKeyboardButton("Кроме выходных (Пн-Пт)", callback_data="days_weekdays")],
        [InlineKeyboardButton("Выбрать конкретные дни", callback_data="days_specific")]
    ])

def get_specific_days_keyboard(selected_days: list):
    """Возвращает клавиатуру для выбора конкретных дней с подсветкой выбранных."""
    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if 'mon' in selected_days else ''}Пн", callback_data="day_mon"),
         InlineKeyboardButton(f"{'✅ ' if 'tue' in selected_days else ''}Вт", callback_data="day_tue"),
         InlineKeyboardButton(f"{'✅ ' if 'wed' in selected_days else ''}Ср", callback_data="day_wed")],
        [InlineKeyboardButton(f"{'✅ ' if 'thu' in selected_days else ''}Чт", callback_data="day_thu"),
         InlineKeyboardButton(f"{'✅ ' if 'fri' in selected_days else ''}Пт", callback_data="day_fri"),
         InlineKeyboardButton(f"{'✅ ' if 'sat' in selected_days else ''}Сб", callback_data="day_sat")],
        [InlineKeyboardButton(f"{'✅ ' if 'sun' in selected_days else ''}Вс", callback_data="day_sun")],
        [InlineKeyboardButton("✅ Сохранить выбранные дни", callback_data="save_specific_days")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения пользователя в зависимости от состояния."""
    user_id = update.effective_user.id
    user_state = get_user_state(user_id)
    text = update.message.text

    if user_state == 'awaiting_reminder_text':
        if text.startswith('preset_text_'): # Если пользователь нажал на кнопку пресета
            preset_map = {
                "preset_text_charge": "Сделать зарядку",
                "preset_text_motiv": "Посмотреть мотивирующий ролик",
                "preset_text_book": "Почитать книгу",
                "preset_text_plans": "Составить планы на завтрашний день",
            }
            context.user_data['reminder_text'] = preset_map.get(text, text) # используем text для случая если кнопка не найдена (не должна быть)
        else: # Если пользователь ввел свой текст
            context.user_data['reminder_text'] = text
        await ask_for_reminder_days(user_id, context) # Изменен порядок: сначала дни, потом время

    elif user_state == 'awaiting_reminder_time':
        # Простая валидация времени ЧЧ:ММ
        try:
            hour, minute = map(int, text.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
            context.user_data['reminder_time'] = text
            # Все данные собраны, добавляем напоминание
            reminder_text = context.user_data.get('reminder_text')
            reminder_days = context.user_data.get('reminder_days')
            
            if reminder_text and text and reminder_days:
                # Проверяем лимит напоминаний
                if count_active_reminders(user_id) >= 5:
                    await update.message.reply_text("Вы достигли лимита в 5 напоминаний в день. Удалите некоторые, чтобы добавить новые.")
                    save_user_state(user_id, 'start')
                    return
                
                reminder_id = add_reminder_to_db(user_id, reminder_text, text, reminder_days)
                schedule_reminder_job(context, user_id, reminder_id, reminder_text, text, reminder_days)
                await update.message.reply_text(f"Отлично! Напоминание '{reminder_text}' установлено на {text} ({get_days_display_name(reminder_days)}).")
                save_user_state(user_id, 'start')
            else:
                await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте создать напоминание снова с /addreminder.")
                save_user_state(user_id, 'start')

        except ValueError:
            await update.message.reply_text("Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ (например, 14:30).")

    elif user_state == 'awaiting_edit_text':
        reminder_id = context.user_data.get('editing_reminder_id')
        if reminder_id:
            update_reminder_in_db(reminder_id, text=text)
            reminder_data = get_reminder_by_id(reminder_id)
            if reminder_data:
                r_id, u_id, r_text, r_time, r_days = reminder_data
                schedule_reminder_job(context, u_id, r_id, r_text, r_time, r_days) # Перепланируем с новым текстом
            await update.message.reply_text("Текст напоминания успешно обновлен.")
            await show_my_reminders(update, context) # Обновляем список напоминаний
        save_user_state(user_id, 'start')

    elif user_state == 'awaiting_edit_time':
        reminder_id = context.user_data.get('editing_reminder_id')
        try:
            hour, minute = map(int, text.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
            if reminder_id:
                update_reminder_in_db(reminder_id, time=text)
                reminder_data = get_reminder_by_id(reminder_id)
                if reminder_data:
                    r_id, u_id, r_text, r_time, r_days = reminder_data
                    schedule_reminder_job(context, u_id, r_id, r_text, r_time, r_days) # Перепланируем с новым временем
                await update.message.reply_text("Время напоминания успешно обновлено.")
                await show_my_reminders(update, context) # Обновляем список напоминаний
            save_user_state(user_id, 'start')
        except ValueError:
            await update.message.reply_text("Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ (например, 14:30).")

    else:
        # Если бот не в ожидании конкретного ввода, можно предложить список команд
        await update.message.reply_text(
            "Извини, я не понял твою команду. "
            "Пожалуйста, используй кнопки или команды:\n"
            "/start - начать или перезапустить бота\n"
            "/addreminder - добавить новое напоминание\n"
            "/myreminders - показать мои напоминания"
        )

async def add_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /addreminder."""
    user_id = update.effective_user.id
    if count_active_reminders(user_id) >= 5:
        await update.message.reply_text("Вы достигли лимита в 5 напоминаний в день. Удалите некоторые, чтобы добавить новые.")
        return

    await ask_for_reminder_text(user_id, context)

async def show_my_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список напоминаний пользователя."""
    user_id = update.effective_user.id
    reminders = get_reminders_from_db(user_id)

    if not reminders:
        await update.message.reply_text("У вас пока нет активных напоминаний. Используйте /addreminder, чтобы добавить новое.")
        return

    message_text = "Ваши текущие напоминания:\n\n"
    keyboard = []
    for r_id, text, time, days_str in reminders:
        days_display = get_days_display_name(days_str)
        message_text += f"▪️ *{text}* в {time} ({days_display})\n"
        keyboard.append([
            InlineKeyboardButton(f"⚙️ Редактировать '{text}'", callback_data=f"edit_reminder_{r_id}"),
            InlineKeyboardButton(f"🗑️ Удалить '{text}'", callback_data=f"delete_reminder_{r_id}")
        ])
    
    keyboard.append([InlineKeyboardButton("➕ Добавить новое напоминание", callback_data="add_reminder")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем или редактируем сообщение
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')

def get_days_display_name(days_str: str) -> str:
    """Возвращает читаемое название для дней недели."""
    if days_str == 'everyday':
        return 'Каждый день'
    elif days_str == 'weekdays':
        return 'По будням'
    elif days_str.startswith('specific:'):
        specific_days_map = {
            'mon': 'Пн', 'tue': 'Вт', 'wed': 'Ср', 'thu': 'Чт', 'fri': 'Пт', 'sat': 'Сб', 'sun': 'Вс'
        }
        days_codes = days_str[len('specific:'):].split(',')
        return ", ".join([specific_days_map[d.strip()] for d in days_codes])
    return 'Неизвестно'


# --- Загрузка напоминаний при запуске бота ---
async def load_reminders_on_startup(application: Application):
    """Загружает все активные напоминания из БД и планирует их."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT reminder_id, user_id, text, time, days FROM reminders WHERE active = 1')
    active_reminders = cursor.fetchall()
    conn.close()

    for r_id, user_id, text, time, days in active_reminders:
        try:
            # Для scheduler нам нужен контекст, но на старте его нет.
            # Поэтому мы передаем application, чтобы получить bot и далее context
            # Более надежный способ - хранить context.bot в глобальной переменной или получить через application.bot
            schedule_reminder_job(application.job_queue.run_once, user_id, r_id, text, time, days)
        except Exception as e:
            logger.error(f"Ошибка при планировании напоминания {r_id} на старте: {e}")
    logger.info(f"Загружено и запланировано {len(active_reminders)} активных напоминаний.")


def main() -> None:
    """Запускает бота."""
    init_db() # Убедимся, что база данных инициализирована при запуске
    
    application = Application.builder().token(TOKEN).build()

    # Для того чтобы APScheduler мог использовать await, его нужно запускать в asyncio loop
    # application.run_polling() уже запускает asyncio loop
    scheduler.start() # Запускаем планировщик

    # Загружаем напоминания при старте
    # Важно: load_reminders_on_startup должен иметь доступ к application.bot
    # Мы можем запустить его после build() и до run_polling()
    # Или как job_queue.run_once, который имеет доступ к context
    # Для простоты и демонстрации, пока будем передавать application, но более надежно через job_queue
    application.job_queue.run_once(
        lambda context: load_reminders_on_startup_wrapper(context, application),
        when=datetime.timedelta(seconds=5) # Даем боту немного времени на запуск перед загрузкой напоминаний
    )

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addreminder", add_reminder_command))
    application.add_handler(CommandHandler("myreminders", show_my_reminders))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен и готов к работе!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


async def load_reminders_on_startup_wrapper(context: ContextTypes.DEFAULT_TYPE, application: Application):
    """Обертка для запуска load_reminders_on_startup с доступом к контексту."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT reminder_id, user_id, text, time, days FROM reminders WHERE active = 1')
    active_reminders = cursor.fetchall()
    conn.close()

    for r_id, user_id, text, time, days in active_reminders:
        try:
            schedule_reminder_job(context, user_id, r_id, text, time, days)
        except Exception as e:
            logger.error(f"Ошибка при планировании напоминания {r_id} на старте: {e}")
    logger.info(f"Загружено и запланировано {len(active_reminders)} активных напоминаний.")


if __name__ == '__main__':
    main()
