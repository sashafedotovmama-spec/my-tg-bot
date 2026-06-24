import logging
import random
import re
import asyncio
from datetime import datetime
from typing import Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ================= КОНФИГУРАЦИЯ =================
TOKEN = "8626124819:AAHJ85KYI52TaubXK51YsXKBkcEJKcmYkwg"

# Настройки
CAPTCHA_TIMEOUT = 120
CAPTCHA_ATTEMPTS = 3
BAN_ON_FAIL = True

# Хранилище
captcha_sessions: Dict[int, Dict] = {}
verified_users: set = set()
user_messages: Dict[int, list] = {}
user_warnings: Dict[int, int] = {}

# Настройки анти-флуда
SPAM_LIMIT = 5
SPAM_WINDOW = 10
WARN_LIMIT = 3

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= КАПЧА =================
class CaptchaManager:
    @staticmethod
    def generate_captcha() -> Dict:
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        operation = random.choice(['+', '-', '*'])
        
        if operation == '+':
            answer = num1 + num2
            symbol = '+'
        elif operation == '-':
            answer = num1 - num2
            symbol = '−'
        else:
            answer = num1 * num2
            symbol = '×'
        
        return {
            'answer': str(answer),
            'display': f"**{num1} {symbol} {num2}**"
        }
    
    @staticmethod
    def get_captcha_keyboard(user_id: int) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("🔄 Другой пример", callback_data=f"captcha_refresh_{user_id}"),
                InlineKeyboardButton("❌ Выйти", callback_data=f"captcha_exit_{user_id}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_captcha_text(username: str, attempts: int, max_attempts: int, display: str) -> str:
        return (
            f"🔐 **Проверка на бота**\n\n"
            f"👤 {username}, для доступа в чат решите пример:\n\n"
            f"📝 {display}\n\n"
            f"⏱️ У вас {CAPTCHA_TIMEOUT // 60} минут(ы)\n"
            f"📊 Попыток: {attempts}/{max_attempts}\n\n"
            f"_Напишите ответ числом в чат_"
        )

# ================= ТАЙМЕР =================
async def captcha_timeout(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(CAPTCHA_TIMEOUT)
    
    if user_id in captcha_sessions:
        if BAN_ON_FAIL:
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.send_message(
                    chat_id,
                    f"🚫 Пользователь не прошел капчу и был заблокирован.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка бана: {e}")
        
        captcha_sessions.pop(user_id, None)
        verified_users.discard(user_id)
        logger.info(f"⏰ Таймаут капчи для {user_id}")

# ================= ОСНОВНЫЕ ФУНКЦИИ =================
async def send_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int, user):
    """Отправляет капчу пользователю"""
    username = user.mention_html()
    
    captcha_data = CaptchaManager.generate_captcha()
    
    captcha_sessions[user_id] = {
        'answer': captcha_data['answer'],
        'attempts': 0,
        'max_attempts': CAPTCHA_ATTEMPTS,
        'message_id': None,
        'chat_id': chat_id,
        'username': username,
        'display': captcha_data['display'],
        'user_id': user_id
    }
    
    text = CaptchaManager.get_captcha_text(
        username=username,
        attempts=0,
        max_attempts=CAPTCHA_ATTEMPTS,
        display=captcha_data['display']
    )
    keyboard = CaptchaManager.get_captcha_keyboard(user_id)
    
    sent_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    
    captcha_sessions[user_id]['message_id'] = sent_msg.message_id
    
    asyncio.create_task(captcha_timeout(user_id, chat_id, context))
    
    if update and update.message:
        try:
            await update.message.delete()
        except:
            pass
    
    logger.info(f"✅ Отправлена капча для {user_id} в чате {chat_id}")

# ================= ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ =================
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик ВСЕХ сообщений"""
    if not update.message:
        return
    
    user = update.message.from_user
    chat = update.message.chat
    text = update.message.text
    
    # Проверяем, не команда ли это
    if text and text.startswith('/'):
        return
    
    logger.info(f"📩 Сообщение от {user.id} в чате {chat.id}: {text[:30] if text else 'не текст'}")
    
    # ===== 1. ПРОВЕРКА НА КАПЧУ =====
    # Если пользователь в процессе капчи - обрабатываем ответ
    if user.id in captcha_sessions:
        session = captcha_sessions[user.id]
        
        # Проверяем, правильный ли чат
        if session['chat_id'] != chat.id:
            await update.message.delete()
            return
        
        # Если это текстовое сообщение - проверяем ответ на капчу
        if text:
            user_answer = text.strip()
            correct_answer = session['answer']
            
            if user_answer == correct_answer:
                # Успешно!
                verified_users.add(user.id)
                await update.message.delete()
                
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat.id,
                        message_id=session['message_id'],
                        text=f"✅ {session['username']} успешно прошел капчу!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Не удалось обновить сообщение: {e}")
                
                await context.bot.send_message(
                    chat.id,
                    f"👋 Добро пожаловать в чат, {session['username']}!",
                    parse_mode="HTML"
                )
                
                captcha_sessions.pop(user.id, None)
                logger.info(f"✅ Пользователь {user.id} прошел капчу")
                return
            else:
                session['attempts'] += 1
                await update.message.delete()
                
                if session['attempts'] >= session['max_attempts']:
                    if BAN_ON_FAIL:
                        try:
                            await context.bot.ban_chat_member(chat.id, user.id)
                            await context.bot.edit_message_text(
                                chat_id=chat.id,
                                message_id=session['message_id'],
                                text=f"🚫 {session['username']} заблокирован за превышение попыток!",
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"Ошибка бана: {e}")
                    
                    captcha_sessions.pop(user.id, None)
                    logger.info(f"❌ Пользователь {user.id} заблокирован")
                    return
                else:
                    new_captcha = CaptchaManager.generate_captcha()
                    session['answer'] = new_captcha['answer']
                    session['display'] = new_captcha['display']
                    
                    text_captcha = CaptchaManager.get_captcha_text(
                        username=session['username'],
                        attempts=session['attempts'],
                        max_attempts=session['max_attempts'],
                        display=session['display']
                    )
                    keyboard = CaptchaManager.get_captcha_keyboard(user.id)
                    
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat.id,
                            message_id=session['message_id'],
                            text=text_captcha,
                            parse_mode="Markdown",
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось обновить сообщение: {e}")
                    
                    await context.bot.send_message(
                        chat.id,
                        f"❌ Неправильно! Попыток осталось: {session['max_attempts'] - session['attempts']}",
                        parse_mode="HTML"
                    )
                    return
        
        # Если не текст - удаляем
        await update.message.delete()
        return
    
    # ===== 2. ПРОВЕРКА НА НОВОГО ПОЛЬЗОВАТЕЛЯ =====
    # Если пользователь не верифицирован - отправляем капчу
    if user.id not in verified_users:
        # Проверяем, не админ ли это
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status in ['administrator', 'creator']:
                logger.info(f"👑 Пропускаем администратора {user.id}")
                return
            if member.user.is_bot:
                return
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")
            return
        
        logger.info(f"🔐 Пользователь {user.id} не верифицирован, отправляем капчу!")
        await send_captcha(update, context, user.id, chat.id, user)
        await update.message.delete()
        return
    
    # ===== 3. АНТИ-ФЛУД =====
    if await is_spamming(user.id):
        await update.message.delete()
        await warn_user(update, context, user)
        return
    
    # ===== 4. ПРОВЕРКА НА СПАМ =====
    if text:
        if await contains_spam(text):
            await update.message.delete()
            await context.bot.send_message(
                chat.id,
                f"❌ {user.mention_html()}, запрещенный контент!",
                parse_mode="HTML"
            )
            return

# ================= ОБРАБОТЧИКИ СОБЫТИЙ =================
async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает новых участников"""
    logger.info(f"🆕 Событие: новый участник в чате!")
    
    for new_member in update.message.new_chat_members:
        if new_member.is_bot:
            continue
        
        user_id = new_member.id
        chat_id = update.message.chat.id
        
        logger.info(f"👤 Новый участник: {new_member.full_name} (ID: {user_id})")
        
        # Проверяем, не админ ли это
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ['administrator', 'creator']:
                logger.info(f"👑 Пропускаем администратора")
                continue
        except:
            pass
        
        # Очищаем данные
        verified_users.discard(user_id)
        captcha_sessions.pop(user_id, None)
        
        # Отправляем капчу
        await send_captcha(update, context, user_id, chat_id, new_member)
        
        # Удаляем системное сообщение
        try:
            await update.message.delete()
        except:
            pass

async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очищает данные при выходе пользователя"""
    user_id = update.message.left_chat_member.id
    captcha_sessions.pop(user_id, None)
    verified_users.discard(user_id)
    logger.info(f"👋 Пользователь {user_id} вышел, данные очищены")

async def is_spamming(user_id: int) -> bool:
    now = datetime.now()
    
    if user_id not in user_messages:
        user_messages[user_id] = []
    
    user_messages[user_id] = [
        ts for ts in user_messages[user_id]
        if (now - ts).total_seconds() < SPAM_WINDOW
    ]
    
    if len(user_messages[user_id]) >= SPAM_LIMIT:
        return True
    
    user_messages[user_id].append(now)
    return False

async def warn_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user):
    warnings = user_warnings.get(user.id, 0) + 1
    user_warnings[user.id] = warnings
    
    if warnings >= WARN_LIMIT:
        try:
            await context.bot.ban_chat_member(update.message.chat.id, user.id)
            await context.bot.send_message(
                update.message.chat.id,
                f"🚫 {user.mention_html()} заблокирован за флуд!",
                parse_mode="HTML"
            )
            user_messages.pop(user.id, None)
            user_warnings.pop(user.id, None)
        except Exception as e:
            logger.error(f"Ошибка бана: {e}")
    else:
        await context.bot.send_message(
            update.message.chat.id,
            f"⚠️ {user.mention_html()}, не флудите! Предупреждение {warnings}/{WARN_LIMIT}",
            parse_mode="HTML"
        )

async def contains_spam(text: str) -> bool:
    spam_patterns = [
        r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+])+',
        r'казино', r'заработок', r'бесплатно',
        r'реклама', r'промокод', r'скидка',
        r'инвестиции', r'биткоин', r'криптовалюта'
    ]
    
    text_lower = text.lower()
    for pattern in spam_patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False

# ================= КОМАНДЫ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Бот-антиспам с каптчей**\n\n"
        "Защищает группу от спамеров и ботов.\n"
        "Новые участники проходят капчу перед тем, как писать.\n\n"
        "📌 **Команды:**\n"
        "/stats - статистика\n"
        "/status - статус бота",
        parse_mode="Markdown"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 **Статистика:**\n\n"
        f"✅ Прошли капчу: {len(verified_users)}\n"
        f"⏳ В процессе: {len(captcha_sessions)}",
        parse_mode="Markdown"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Бот активен и работает!",
        parse_mode="Markdown"
    )

# ================= ЗАПУСК =================
def main():
    app = Application.builder().token(TOKEN).build()
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("status", status))
    
    # Обработчики событий
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS,
        handle_new_member
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.LEFT_CHAT_MEMBER,
        handle_left_member
    ))
    
    # ОДИН обработчик для ВСЕХ сообщений
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_all_messages
    ))
    
    # Callback кнопок
    app.add_handler(CallbackQueryHandler(captcha_refresh, pattern="^captcha_refresh_"))
    app.add_handler(CallbackQueryHandler(captcha_exit, pattern="^captcha_exit_"))
    
    print("=" * 50)
    print("🤖 БОТ-АНТИСПАМ С КАПТЧЕЙ ЗАПУЩЕН!")
    print("=" * 50)
    print("✅ Используется ЕДИНЫЙ обработчик сообщений")
    print("✅ Капча отправляется при входе и при первом сообщении")
    print("=" * 50)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

# ================= ОБРАБОТЧИКИ CALLBACK =================
async def captcha_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = int(query.data.split('_')[2])
    
    if user_id not in captcha_sessions:
        await query.edit_message_text("⏳ Сессия истекла.")
        return
    
    session = captcha_sessions[user_id]
    new_captcha = CaptchaManager.generate_captcha()
    session['answer'] = new_captcha['answer']
    session['display'] = new_captcha['display']
    
    text = CaptchaManager.get_captcha_text(
        username=session['username'],
        attempts=session['attempts'],
        max_attempts=session['max_attempts'],
        display=session['display']
    )
    keyboard = CaptchaManager.get_captcha_keyboard(user_id)
    
    await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def captcha_exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = int(query.data.split('_')[2])
    
    if user_id in captcha_sessions:
        captcha_sessions.pop(user_id, None)
        await query.edit_message_text("❌ Вы вышли из проверки.")

if __name__ == "__main__":
    main()
