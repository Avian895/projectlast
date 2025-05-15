import os
import json
import random
import chess
import requests
import logging
from aiogram.client.bot import DefaultBotProperties
import chess.svg
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Message,
)
from aiogram.utils.markdown import bold, code, italic
from collections import defaultdict
from datetime import timedelta, datetime
from time import sleep
from io import BytesIO
from typing import Optional, Dict, Any
import pytz
from geopy.geocoders import Nominatim
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
import io

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Конфигурация
API_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
WEATHER_API_KEY = "YOUR_OPENWEATHERMAP_API_KEY"
MAPS_API_KEY = "YOUR_GOOGLE_MAPS_API_KEY"
TRANSLATE_API_KEY = "YOUR_YANDEX_TRANSLATE_API_KEY"

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Данные сохраняются в эти файлы
DATA_FILE = "users_data.json"
CHECKS_FILE = "checks_data.json"
GAME_STATES = {}
USER_STATES = {}  # Для хранения состояний пользователей
WEATHER_CACHE = {}


# Загрузка данных
def load_data():
    global users, checks
    try:
        with open(DATA_FILE, "r") as file:
            users = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        users = {}

    try:
        with open(CHECKS_FILE, "r") as file:
            checks = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        checks = {}


def save_data():
    with open(DATA_FILE, "w") as file:
        json.dump(users, file, ensure_ascii=False, indent=4)

    with open(CHECKS_FILE, "w") as file:
        json.dump(checks, file, ensure_ascii=False, indent=4)


# Загружаем данные при старте
load_data()


# Класс для работы с погодой
class WeatherAPI:
    @staticmethod
    async def get_weather(city: str) -> Dict[str, Any]:
        """Получение данных о погоде через OpenWeatherMap API"""
        try:
            # Проверяем кэш
            if city in WEATHER_CACHE:
                cached_data = WEATHER_CACHE[city]
                if (datetime.now() - cached_data["timestamp"]).seconds < 3600:  # 1 час кэша
                    return cached_data["data"]

            url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()

            # Сохраняем в кэш
            WEATHER_CACHE[city] = {
                "timestamp": datetime.now(),
                "data": data
            }

            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Weather API error: {e}")
            return {"error": str(e)}

    @staticmethod
    def format_weather(data: Dict[str, Any]) -> str:
        """Форматирование данных о погоде в читаемый текст"""
        if "error" in data:
            return f"Ошибка при получении погоды: {data['error']}"

        try:
            city = data["name"]
            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]
            description = data["weather"][0]["description"].capitalize()
            icon = data["weather"][0]["icon"]

            # Получаем время восхода и заката
            timezone = pytz.timezone("UTC")
            sunrise = datetime.fromtimestamp(data["sys"]["sunrise"], timezone).strftime("%H:%M")
            sunset = datetime.fromtimestamp(data["sys"]["sunset"], timezone).strftime("%H:%M")

            weather_icons = {
                "01": "☀️",  # ясно
                "02": "⛅️",  # малооблачно
                "03": "☁️",  # облачно
                "04": "☁️",  # пасмурно
                "09": "🌧️",  # дождь
                "10": "🌦️",  # дождь с прояснениями
                "11": "⛈️",  # гроза
                "13": "❄️",  # снег
                "50": "🌫️",  # туман
            }

            icon_code = icon[:-1]
            emoji = weather_icons.get(icon_code, "🌡️")

            return (
                f"{emoji} Погода в {city}:\n"
                f"{description}\n"
                f"Температура: {temp}°C (ощущается как {feels_like}°C)\n"
                f"Влажность: {humidity}%\n"
                f"Ветер: {wind_speed} м/с\n"
                f"Восход: {sunrise} 🌅\n"
                f"Закат: {sunset} 🌇"
            )
        except KeyError as e:
            logger.error(f"Error formatting weather data: {e}")
            return "Не удалось обработать данные о погоде."


# Класс для работы с картами
class MapsAPI:
    @staticmethod
    async def get_map_image(location: str, zoom: int = 12, size: str = "600x400") -> Optional[bytes]:
        """Получение статического изображения карты через Google Maps API"""
        try:
            # Сначала получаем координаты по названию места
            geolocator = Nominatim(user_agent="telegram_bot")
            location_data = geolocator.geocode(location)
            if not location_data:
                return None

            lat, lon = location_data.latitude, location_data.longitude

            url = f"https://maps.googleapis.com/maps/api/staticmap?center={lat},{lon}&zoom={zoom}&size={size}&maptype=roadmap&markers=color:red%7C{lat},{lon}&key={MAPS_API_KEY}"
            response = requests.get(url)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Maps API error: {e}")
            return None

    @staticmethod
    async def get_route_map(origin: str, destination: str, mode: str = "driving") -> Optional[bytes]:
        """Получение карты с маршрутом"""
        try:
            geolocator = Nominatim(user_agent="telegram_bot")
            origin_data = geolocator.geocode(origin)
            destination_data = geolocator.geocode(destination)

            if not origin_data or not destination_data:
                return None

            origin_lat, origin_lon = origin_data.latitude, origin_data.longitude
            dest_lat, dest_lon = destination_data.latitude, destination_data.longitude

            url = f"https://maps.googleapis.com/maps/api/staticmap?size=600x400&maptype=roadmap&markers=color:green%7C{origin_lat},{origin_lon}&markers=color:red%7C{dest_lat},{dest_lon}&path=color:0x0000ff80|weight:5|{origin_lat},{origin_lon}|{dest_lat},{dest_lon}&key={MAPS_API_KEY}"
            response = requests.get(url)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Route Map API error: {e}")
            return None


# Класс для работы с переводом
class TranslateAPI:
    @staticmethod
    async def translate_text(text: str, target_lang: str = "ru") -> Optional[str]:
        """Перевод текста через Yandex Translate API"""
        try:
            url = "https://translate.yandex.net/api/v1.5/tr.json/translate"
            params = {
                "key": TRANSLATE_API_KEY,
                "text": text,
                "lang": target_lang,
            }
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return " ".join(data["text"])
        except Exception as e:
            logger.error(f"Translate API error: {e}")
            return None


# Класс для работы с графиками
class ChartGenerator:
    @staticmethod
    async def generate_irisky_chart(user_data: Dict[str, Any]) -> Optional[bytes]:
        """Генерация графика изменения баланса пайкоинов"""
        try:
            history = user_data.get("irisky_history", [])
            if not history:
                return None

            dates = [datetime.fromisoformat(item["date"]) for item in history]
            values = [item["amount"] for item in history]

            plt.figure(figsize=(10, 5))
            plt.plot(dates, values, marker="o", linestyle="-", color="blue")
            plt.title("История изменения баланса пайкоинов")
            plt.xlabel("Дата")
            plt.ylabel("Количество пайкоинов")
            plt.grid(True)
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format="png")
            buf.seek(0)
            plt.close()
            return buf.read()
        except Exception as e:
            logger.error(f"Chart generation error: {e}")
            return None


# Класс для шахматной игры
class ChessGame:
    def __init__(self, white_player: str, black_player: str):
        self.board = chess.Board()
        self.white_player = white_player
        self.black_player = black_player
        self.current_turn = "white"
        self.moves_history = []
        self.start_time = datetime.now()

    def show_board(self) -> str:
        """Возвращает SVG-представление доски"""
        return chess.svg.board(
            board=self.board,
            orientation=chess.WHITE if self.current_turn == "white" else chess.BLACK,
            size=400,
            lastmove=self.board.peek() if self.board.move_stack else None,
            check=self.board.king(
                chess.WHITE if self.board.turn == chess.WHITE else chess.BLACK) if self.board.is_check() else None,
        )

    def move(self, move_str: str) -> bool:
        """Пытается выполнить ход, возвращает успешность выполнения"""
        try:
            move = self.board.parse_san(move_str)
            if move in self.board.legal_moves:
                self.board.push(move)
                self.moves_history.append(move_str)
                self.current_turn = "black" if self.current_turn == "white" else "white"
                return True
            return False
        except (chess.IllegalMoveError, chess.InvalidMoveError):
            return False

    def winner(self) -> Optional[str]:
        """Определяет победителя игры"""
        if self.board.is_checkmate():
            return self.white_player if self.current_turn == "black" else self.black_player
        if self.board.is_stalemate() or self.board.is_insufficient_material():
            return "Draw"
        return None

    def get_game_status(self) -> str:
        """Возвращает текстовое состояние игры"""
        if self.board.is_checkmate():
            return "Мат! Победитель: " + ("Белые" if self.current_turn == "black" else "Чёрные")
        if self.board.is_stalemate():
            return "Пат - ничья!"
        if self.board.is_insufficient_material():
            return "Недостаточно материала для мата - ничья!"
        if self.board.is_check():
            return "Шах!"
        return "Игра продолжается"

    def get_game_duration(self) -> str:
        """Возвращает продолжительность игры"""
        duration = datetime.now() - self.start_time
        hours, remainder = divmod(duration.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"


# Класс для игры в шашки
class CheckersGame:
    def __init__(self, player1: str, player2: str):
        self.board = self.create_board()
        self.player1 = player1
        self.player2 = player2
        self.current_player = player1
        self.moves_history = []
        self.start_time = datetime.now()

    @staticmethod
    def create_board() -> Dict[str, Any]:
        """Создает начальную доску для шашек"""
        board = {}
        for row in range(8):
            for col in range(8):
                if (row + col) % 2 == 1:
                    if row < 3:
                        board[f"{row}{col}"] = {"type": "pawn", "player": 2}
                    elif row > 4:
                        board[f"{row}{col}"] = {"type": "pawn", "player": 1}
                    else:
                        board[f"{row}{col}"] = None
                else:
                    board[f"{row}{col}"] = None
        return board

    def move(self, from_pos: str, to_pos: str) -> bool:
        """Пытается выполнить ход, возвращает успешность выполнения"""
        # Упрощенная логика хода (без учета правил шашек)
        if from_pos in self.board and to_pos in self.board:
            piece = self.board[from_pos]
            if piece and ((piece["player"] == 1 and self.current_player == self.player1) or
                          (piece["player"] == 2 and self.current_player == self.player2)):
                self.board[to_pos] = piece
                self.board[from_pos] = None
                self.moves_history.append(f"{from_pos}-{to_pos}")
                self.current_player = self.player2 if self.current_player == self.player1 else self.player1
                return True
        return False

    def show_board(self) -> str:
        """Генерирует ASCII-представление доски"""
        board_str = "  0 1 2 3 4 5 6 7\n"
        for row in range(8):
            board_str += f"{row} "
            for col in range(8):
                pos = f"{row}{col}"
                piece = self.board[pos]
                if piece is None:
                    board_str += ". "
                elif piece["player"] == 1:
                    board_str += "○ " if piece["type"] == "pawn" else "Ⓞ "
                else:
                    board_str += "● " if piece["type"] == "pawn" else "◉ "
            board_str += "\n"
        return board_str

    def winner(self) -> Optional[str]:
        """Определяет победителя игры (упрощенная версия)"""
        p1_pieces = sum(1 for piece in self.board.values() if piece and piece["player"] == 1)
        p2_pieces = sum(1 for piece in self.board.values() if piece and piece["player"] == 2)

        if p1_pieces == 0:
            return self.player2
        if p2_pieces == 0:
            return self.player1
        return None


# Класс для работы с квизами
class QuizManager:
    QUIZZES = {
        "general": [
            {
                "question": "Какая столица Франции?",
                "options": ["Лондон", "Берлин", "Париж", "Мадрид"],
                "answer": 2,
            },
            {
                "question": "Сколько планет в Солнечной системе?",
                "options": ["7", "8", "9", "10"],
                "answer": 1,
            },
            {
                "question": "Кто написал 'Войну и мир'?",
                "options": ["Достоевский", "Толстой", "Чехов", "Тургенев"],
                "answer": 1,
            },
        ],
        "science": [
            {
                "question": "Какой химический элемент обозначается как 'O'?",
                "options": ["Золото", "Кислород", "Осмий", "Олово"],
                "answer": 1,
            },
            {
                "question": "Какая самая большая планета Солнечной системы?",
                "options": ["Земля", "Юпитер", "Сатурн", "Нептун"],
                "answer": 1,
            },
        ],
    }

    @classmethod
    def get_quiz(cls, category: str = "general") -> Optional[Dict[str, Any]]:
        """Возвращает случайный вопрос из указанной категории"""
        if category not in cls.QUIZZES or not cls.QUIZZES[category]:
            return None
        return random.choice(cls.QUIZZES[category])


# Класс для работы с напоминаниями
class ReminderManager:
    @staticmethod
    async def set_reminder(user_id: int, text: str, remind_time: datetime) -> bool:
        """Устанавливает напоминание для пользователя"""
        try:
            if "reminders" not in users[str(user_id)]:
                users[str(user_id)]["reminders"] = []

            users[str(user_id)]["reminders"].append({
                "text": text,
                "time": remind_time.isoformat(),
                "created": datetime.now().isoformat(),
                "completed": False
            })
            save_data()
            return True
        except Exception as e:
            logger.error(f"Error setting reminder: {e}")
            return False

    @staticmethod
    async def check_reminders() -> None:
        """Проверяет и отправляет напоминания, которые наступили"""
        try:
            current_time = datetime.now()
            for user_id, user_data in users.items():
                if "reminders" not in user_data:
                    continue

                for reminder in user_data["reminders"]:
                    if reminder["completed"]:
                        continue

                    remind_time = datetime.fromisoformat(reminder["time"])
                    if remind_time <= current_time:
                        try:
                            await bot.send_message(
                                user_id,
                                f"⏰ Напоминание: {reminder['text']}\n"
                                f"Установлено: {datetime.fromisoformat(reminder['created']).strftime('%Y-%m-%d %H:%M')}"
                            )
                            reminder["completed"] = True
                        except Exception as e:
                            logger.error(f"Error sending reminder to {user_id}: {e}")

            save_data()
        except Exception as e:
            logger.error(f"Error checking reminders: {e}")


# Класс для работы с пайкоинами (экономика бота)
class IriskyEconomy:
    @staticmethod
    async def add_irisky(user_id: int, amount: int, reason: str = "") -> None:
        """Добавляет пайкоины пользователю"""
        try:
            user_id = str(user_id)
            if user_id not in users:
                users[user_id] = {
                    "username": "",
                    "messages_count": 0,
                    "warnings": [],
                    "ban_expiry": None,
                    "irisky": 0,
                    "is_moderator": False,
                    "irisky_history": [],
                }

            users[user_id]["irisky"] = users[user_id].get("irisky", 0) + amount

            # Записываем в историю
            users[user_id]["irisky_history"].append({
                "date": datetime.now().isoformat(),
                "amount": amount,
                "balance": users[user_id]["irisky"],
                "reason": reason
            })

            # Ограничиваем размер истории
            if len(users[user_id]["irisky_history"]) > 50:
                users[user_id]["irisky_history"] = users[user_id]["irisky_history"][-50:]

            save_data()
        except Exception as e:
            logger.error(f"Error adding irisky to {user_id}: {e}")

    @staticmethod
    async def transfer_irisky(from_user_id: int, to_user_id: int, amount: int) -> bool:
        """Переводит пайкоины между пользователями"""
        try:
            from_user_id = str(from_user_id)
            to_user_id = str(to_user_id)

            if from_user_id not in users or to_user_id not in users:
                return False

            if users[from_user_id]["irisky"] < amount:
                return False

            users[from_user_id]["irisky"] -= amount
            users[to_user_id]["irisky"] += amount

            # Записываем в историю
            users[from_user_id]["irisky_history"].append({
                "date": datetime.now().isoformat(),
                "amount": -amount,
                "balance": users[from_user_id]["irisky"],
                "reason": f"Перевод пользователю {to_user_id}"
            })

            users[to_user_id]["irisky_history"].append({
                "date": datetime.now().isoformat(),
                "amount": amount,
                "balance": users[to_user_id]["irisky"],
                "reason": f"Перевод от пользователя {from_user_id}"
            })

            save_data()
            return True
        except Exception as e:
            logger.error(f"Error transferring irisky: {e}")
            return False

    @staticmethod
    async def create_check(user_id: int, amount: int) -> Optional[str]:
        """Создает чек на указанное количество пайкоинов"""
        try:
            user_id = str(user_id)
            if user_id not in users or users[user_id]["irisky"] < amount:
                return None

            check_code = ''.join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=8))

            if check_code in checks:
                return None  # На случай коллизии

            checks[check_code] = {
                "user_id": user_id,
                "amount": amount,
                "created": datetime.now().isoformat(),
                "activated": False
            }

            # Снимаем пайкоины у создателя
            users[user_id]["irisky"] -= amount

            # Записываем в историю
            users[user_id]["irisky_history"].append({
                "date": datetime.now().isoformat(),
                "amount": -amount,
                "balance": users[user_id]["irisky"],
                "reason": f"Создание чека {check_code}"
            })

            save_data()
            return check_code
        except Exception as e:
            logger.error(f"Error creating check: {e}")
            return None

    @staticmethod
    async def activate_check(user_id: int, check_code: str) -> Optional[int]:
        """Активирует чек и возвращает количество полученных пайкоинов"""
        try:
            user_id = str(user_id)
            check_code = check_code.upper()

            if check_code not in checks or checks[check_code]["activated"]:
                return None

            amount = checks[check_code]["amount"]
            creator_id = checks[check_code]["user_id"]

            # Если пользователь пытается активировать свой чек
            if creator_id == user_id:
                # Возвращаем пайкоины обратно
                users[user_id]["irisky"] += amount

                # Записываем в историю
                users[user_id]["irisky_history"].append({
                    "date": datetime.now().isoformat(),
                    "amount": amount,
                    "balance": users[user_id]["irisky"],
                    "reason": f"Отмена чека {check_code}"
                })

                del checks[check_code]
                save_data()
                return None

            # Переводим пайкоины новому пользователю
            users[user_id]["irisky"] += amount

            # Записываем в историю
            users[user_id]["irisky_history"].append({
                "date": datetime.now().isoformat(),
                "amount": amount,
                "balance": users[user_id]["irisky"],
                "reason": f"Активация чека {check_code}"
            })

            checks[check_code]["activated"] = True
            checks[check_code]["activated_by"] = user_id
            checks[check_code]["activated_at"] = datetime.now().isoformat()

            save_data()
            return amount
        except Exception as e:
            logger.error(f"Error activating check: {e}")
            return None


# ================== ХЕНДЛЕРЫ КОМАНД ==================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users:
        users[uid] = {
            "username": message.from_user.username or message.from_user.full_name,
            "messages_count": 0,
            "warnings": [],
            "ban_expiry": None,
            "irisky": 100,  # Начальный бонус
            "is_moderator": False,
            "irisky_history": [{
                "date": datetime.now().isoformat(),
                "amount": 100,
                "balance": 100,
                "reason": "Начальный бонус"
            }],
            "reminders": [],
            "last_ferma": None,
        }
        save_data()

    # Создаем клавиатуру с основными командами
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎮 Игры"), KeyboardButton(text="🌤️ Погода")],
            [KeyboardButton(text="💰 Пайкоины"), KeyboardButton(text="📊 Профиль")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="🎯 Викторина")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )

    await message.answer(
        f"👋 Добро пожаловать, {bold(message.from_user.full_name)}!\n\n"
        f"Я - многофункциональный бот с играми, погодой, экономикой и другими функциями.\n"
        f"Используйте кнопки ниже или команду /help для списка возможностей.\n\n"
        f"Ваш стартовый баланс: {bold('100 пайкоинов')}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    commands_list = """📚 Доступные команды:

🎮 Игры:
/game_chess - Начать шахматную партию
/game_checkers - Начать игру в шашки
/move [ход] - Сделать ход в текущей игре
/end_game - Завершить текущую игру

🌍 Информация:
/weather [город] - Узнать погоду
/map [место] - Показать карту места
/route [откуда] [куда] - Построить маршрут
/translate [текст] - Перевести текст на русский

💰 Экономика:
/get_irisky - Узнать баланс
/transfer [ID] [сумма] - Перевести пайкоины
/create_check [сумма] - Создать чек
/activate_check [код] - Активировать чек
/irisky_history - История операций
/ferma - Получить пайкоины (раз в день)

📅 Утилиты:
/remind [время] [текст] - Установить напоминание
/quiz - Начать викторину
/who [текст] - Случайный выбор участника

👮‍♂️ Модерация (для модераторов):
/warn [ID] - Выдать предупреждение
/ban [ID] [причина] - Забанить пользователя
/unban [ID] - Разбанить пользователя
/clearwarns [ID] - Снять предупреждения

ℹ Прочее:
/profile - Ваш профиль
/statistics - Статистика чата
/real_life - Полезные советы
"""

    # Создаем инлайн-кнопки для быстрого доступа
    inline_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎮 Игры", callback_data="games"),
                InlineKeyboardButton(text="🌤️ Погода", callback_data="weather"),
            ],
            [
                InlineKeyboardButton(text="💰 Пайкоины", callback_data="irisky"),
                InlineKeyboardButton(text="📊 Профиль", callback_data="profile"),
            ],
        ]
    )

    await message.answer(commands_list, reply_markup=inline_keyboard)


@dp.message(F.text.lower() == "❓ помощь")
async def help_button(message: types.Message):
    await cmd_help(message)


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    uid = str(message.from_user.id)
    if uid in users:
        user = users[uid]

        # Формируем текст профиля
        profile_text = (
            f"👤 {bold('Профиль пользователя')}\n\n"
            f"🆔 ID: {code(uid)}\n"
            f"📛 Имя: {user['username']}\n"
            f"📨 Сообщений: {user['messages_count']}\n"
            f"💰 Пайкоины: {bold(str(user['irisky']))}\n"
            f"⚠ Предупреждений: {len(user['warnings'])}\n"
        )

        # Добавляем информацию о модераторе, если есть
        if user.get("is_moderator", False):
            profile_text += "\n⭐ Вы модератор этого чата\n"

        # Добавляем информацию о бане, если есть
        if user.get("ban_expiry"):
            ban_time = datetime.fromisoformat(user["ban_expiry"])
            if ban_time > datetime.now():
                profile_text += f"\n🚫 Заблокирован до: {ban_time.strftime('%Y-%m-%d %H:%M')}\n"

        # Генерируем график истории пайкоинов
        chart = await ChartGenerator.generate_irisky_chart(user)
        if chart:
            photo = BufferedInputFile(chart, filename="chart.png")
            await message.answer_photo(photo, caption=profile_text, parse_mode=ParseMode.HTML)
        else:
            await message.answer(profile_text, parse_mode=ParseMode.HTML)
    else:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")


@dp.message(F.text.lower() == "📊 профиль")
async def profile_button(message: types.Message):
    await cmd_profile(message)


@dp.message(Command("get_irisky"))
async def cmd_get_irisky(message: types.Message):
    uid = str(message.from_user.id)
    if uid in users:
        irisky = users[uid]["irisky"]
        await message.answer(f"💰 Ваш текущий баланс: {bold(str(irisky))} пайкоинов", parse_mode=ParseMode.HTML)
    else:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")


@dp.message(F.text.lower() == "💰 пайкоины")
async def irisky_button(message: types.Message):
    await cmd_get_irisky(message)


@dp.message(Command("irisky_history"))
async def cmd_irisky_history(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")
        return

    history = users[uid].get("irisky_history", [])
    if not history:
        await message.answer("История операций с пайкоинами пуста.")
        return

    # Формируем текст истории (последние 10 операций)
    history_text = "📊 История операций с пайкоинами (последние 10):\n\n"
    for item in history[-10:]:
        date = datetime.fromisoformat(item["date"]).strftime("%d.%m %H:%M")
        amount = item["amount"]
        balance = item["balance"]
        reason = item.get("reason", "")

        history_text += (
            f"{date} - {'+' if amount > 0 else ''}{amount} (Баланс: {balance})\n"
            f"Причина: {reason}\n\n"
        )

    await message.answer(history_text)


@dp.message(Command("transfer"))
async def cmd_transfer(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")
        return

    try:
        args = message.text.split()
        if len(args) < 3:
            await message.answer("Использование: /transfer [ID_получателя] [сумма]")
            return

        recipient_id = args[1]
        amount = int(args[2])

        if amount <= 0:
            await message.answer("Сумма перевода должна быть положительной.")
            return

        if recipient_id == uid:
            await message.answer("Нельзя переводить пайкоины самому себе.")
            return

        if users[uid]["irisky"] < amount:
            await message.answer("Недостаточно пайкоинов для перевода.")
            return

        if await IriskyEconomy.transfer_irisky(int(uid), int(recipient_id), amount):
            recipient_name = users.get(recipient_id, {}).get("username", recipient_id)
            await message.answer(
                f"✅ Успешно переведено {bold(str(amount))} пайкоинов пользователю {recipient_name}.",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer("Ошибка перевода. Проверьте ID получателя.")
    except ValueError:
        await message.answer("Неверный формат суммы. Используйте целое число.")


@dp.message(Command("create_check"))
async def cmd_create_check(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")
        return

    try:
        amount = int(message.text.split(maxsplit=1)[1])
        if amount <= 0:
            await message.answer("Сумма чека должна быть положительной.")
            return

        if users[uid]["irisky"] < amount:
            await message.answer("Недостаточно пайкоинов для создания чека.")
            return

        check_code = await IriskyEconomy.create_check(int(uid), amount)
        if check_code:
            await message.answer(
                f"✅ Чек создан!\n\n"
                f"🔢 Код чека: {code(check_code)}\n"
                f"💰 Сумма: {bold(str(amount))} пайкоинов\n\n"
                f"Передайте этот код другому пользователю для активации командой /activate_check {check_code}",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer("Ошибка при создании чека.")
    except (ValueError, IndexError):
        await message.answer("Использование: /create_check [сумма]")


@dp.message(Command("activate_check"))
async def cmd_activate_check(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")
        return

    try:
        check_code = message.text.split(maxsplit=1)[1].strip().upper()
        amount = await IriskyEconomy.activate_check(int(uid), check_code)

        if amount is None:
            await message.answer("Не удалось активировать чек. Проверьте код или попробуйте другой чек.")
        else:
            await message.answer(
                f"🎉 Чек активирован!\n\n"
                f"💰 Вы получили {bold(str(amount))} пайкоинов!",
                parse_mode=ParseMode.HTML
            )
    except IndexError:
        await message.answer("Использование: /activate_check [код_чека]")


@dp.message(Command("ferma"))
async def cmd_ferma(message: types.Message):
    uid = str(message.from_user.id)
    if uid not in users:
        await message.answer("Пользователь не зарегистрирован. Напишите /start для регистрации.")
        return

    # Проверяем, когда пользователь последний раз использовал ферму
    last_ferma = users[uid].get("last_ferma")
    if last_ferma:
        last_time = datetime.fromisoformat(last_ferma)
        if (datetime.now() - last_time) < timedelta(hours=24):
            next_time = last_time + timedelta(hours=24)
            await message.answer(
                f"⏳ Вы уже собирали пайкоины сегодня.\n"
                f"Следующий сбор будет доступен {next_time.strftime('%Y-%m-%d в %H:%M')}."
            )
            return

    # Определяем награду с учетом праздников
    now = datetime.now()
    holidays = [
        datetime(now.year, 1, 1),  # Новый год
        datetime(now.year, 1, 7),  # Рождество
        datetime(now.year, 2, 23),  # День защитника Отечества
        datetime(now.year, 3, 8),  # Международный женский день
        datetime(now.year, 5, 1),  # Праздник весны и труда
        datetime(now.year, 5, 9),  # День Победы
        datetime(now.year, 6, 12),  # День России
        datetime(now.year, 11, 4),  # День народного единства
        datetime(now.year, 12, 31)  # Канун Нового года
    ]

    base_reward = random.randint(10, 30)
    holiday_bonus = 0
    holiday_name = ""

    for date in holidays:
        if now.date() == date.date():
            holiday_bonus = random.randint(20, 50)
            holiday_names = {
                1: "🎄 Новый год",
                7: "🎄 Рождество",
                23: "🎖️ День защитника Отечества",
                8: "🌸 Международный женский день",
                1: "🌷 Праздник весны и труда",
                9: "🎖️ День Победы",
                12: "🇷🇺 День России",
                4: "🤝 День народного единства",
                31: "🎄 Канун Нового года",
            }
            holiday_name = holiday_names.get(date.day, "Праздник")
            break

    total_reward = base_reward + holiday_bonus

    # Добавляем пайкоины пользователю
    await IriskyEconomy.add_irisky(int(uid), total_reward, "Ферма")
    users[uid]["last_ferma"] = datetime.now().isoformat()
    save_data()

    # Формируем ответ
    response = (
        f"🌾 Вы собрали урожай пайкоинов!\n\n"
        f"💰 Получено: {bold(str(total_reward))}\n"
        f"• Базовая награда: {base_reward}\n"
    )

    if holiday_bonus > 0:
        response += f"• Праздничный бонус ({holiday_name}): +{holiday_bonus}\n"

    response += (
        f"\n💵 Ваш текущий баланс: {bold(str(users[uid]['irisky']))}\n"
        f"⏳ Следующий сбор будет доступен через 24 часа."
    )

    await message.answer(response, parse_mode=ParseMode.HTML)


@dp.message(Command("weather"))
async def cmd_weather(message: types.Message):
    try:
        city = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.answer("Укажите город: /weather [город]")
        return

    # Устанавливаем состояние ожидания для пользователя
    USER_STATES[message.from_user.id] = {"waiting_for": "weather", "city": city}

    # Создаем клавиатуру с вариантами
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Текущая погода", callback_data=f"weather_current_{city}"),
                InlineKeyboardButton(text="Прогноз на 3 дня", callback_data=f"weather_forecast_{city}"),
            ],
            [
                InlineKeyboardButton(text="Карта города", callback_data=f"weather_map_{city}"),
            ]
        ]
    )

    await message.answer(
        f"🌤️ Какие данные о погоде в {city} вас интересуют?",
        reply_markup=keyboard
    )


@dp.message(F.text.lower() == "🌤️ погода")
async def weather_button(message: types.Message):
    await message.answer("Введите название города для получения погоды:")


@dp.callback_query(F.data.startswith("weather_"))
async def weather_callback_handler(callback: types.CallbackQuery):
    action, city = callback.data.split("_")[1], callback.data.split("_", 2)[2]

    if action == "current":
        weather_data = await WeatherAPI.get_weather(city)
        weather_text = WeatherAPI.format_weather(weather_data)
        await callback.message.answer(weather_text)
    elif action == "forecast":
        # Здесь можно реализовать получение прогноза
        await callback.message.answer(f"🚧 Прогноз погоды для {city} временно недоступен. Работаем над этим!")
    elif action == "map":
        map_image = await MapsAPI.get_map_image(city)
        if map_image:
            photo = BufferedInputFile(map_image, filename="map.png")
            await callback.message.answer_photo(photo, caption=f"🗺 Карта {city}")
        else:
            await callback.message.answer(f"Не удалось найти карту для {city}")

    await callback.answer()


@dp.message(Command("map"))
async def cmd_map(message: types.Message):
    try:
        location = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.answer("Укажите место: /map [место]")
        return

    # Показываем статус обработки
    processing_msg = await message.answer(f"🔄 Поиск карты для {location}...")

    map_image = await MapsAPI.get_map_image(location)
    if map_image:
        photo = BufferedInputFile(map_image, filename="map.png")
        await message.answer_photo(photo, caption=f"🗺 Карта {location}")
        await processing_msg.delete()
    else:
        await processing_msg.edit_text(f"Не удалось найти карту для {location}")


@dp.message(Command("route"))
async def cmd_route(message: types.Message):
    try:
        args = message.text.split(maxsplit=2)
        origin = args[1]
        destination = args[2]
    except IndexError:
        await message.answer("Использование: /route [откуда] [куда]")
        return

    processing_msg = await message.answer(f"🔄 Построение маршрута из {origin} в {destination}...")

    route_image = await MapsAPI.get_route_map(origin, destination)
    if route_image:
        photo = BufferedInputFile(route_image, filename="route.png")
        await message.answer_photo(
            photo,
            caption=f"🛣 Маршрут из {origin} в {destination}",
        )
        await processing_msg.delete()
    else:
        await processing_msg.edit_text(f"Не удалось построить маршрут из {origin} в {destination}")


@dp.message(Command("translate"))
async def cmd_translate(message: types.Message):
    try:
        text = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.answer("Использование: /translate [текст]")
        return

    processing_msg = await message.answer("🔄 Перевод текста...")

    translated = await TranslateAPI.translate_text(text)
    if translated:
        await processing_msg.edit_text(
            f"🌍 Перевод:\n\n"
            f"📌 Оригинал: {text}\n\n"
            f"🇷🇺 Перевод: {translated}"
        )
    else:
        await processing_msg.edit_text("Не удалось выполнить перевод. Попробуйте позже.")


@dp.message(Command("quiz"))
async def cmd_quiz(message: types.Message):
    quiz = QuizManager.get_quiz()
    if not quiz:
        await message.answer("В данный момент нет доступных вопросов.")
        return

    # Сохраняем текущий вопрос для пользователя
    USER_STATES[message.from_user.id] = {
        "waiting_for": "quiz_answer",
        "quiz": quiz,
        "correct_answer": quiz["answer"],
    }

    # Создаем клавиатуру с вариантами ответов
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=option, callback_data=f"quiz_{idx}")]
            for idx, option in enumerate(quiz["options"])
        ]
    )

    await message.answer(
        f"🎯 Вопрос викторины:\n\n{quiz['question']}",
        reply_markup=keyboard
    )


@dp.message(F.text.lower() == "🎯 викторина")
async def quiz_button(message: types.Message):
    await cmd_quiz(message)


@dp.callback_query(F.data.startswith("quiz_"))
async def quiz_callback_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in USER_STATES or USER_STATES[user_id].get("waiting_for") != "quiz_answer":
        await callback.answer("Время ответа истекло. Начните новую викторину.")
        return

    answer_idx = int(callback.data.split("_")[1])
    quiz = USER_STATES[user_id]["quiz"]
    correct_idx = USER_STATES[user_id]["correct_answer"]

    if answer_idx == correct_idx:
        # Награждаем пользователя за правильный ответ
        reward = random.randint(5, 15)
        await IriskyEconomy.add_irisky(user_id, reward, "Правильный ответ в викторине")

        await callback.message.edit_text(
            f"✅ {bold('Правильно!')}\n\n"
            f"Верный ответ: {quiz['options'][correct_idx]}\n\n"
            f"🎉 Вы получили {reward} пайкоинов!",
            parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(
            f"❌ {bold('Неправильно!')}\n\n"
            f"Правильный ответ: {quiz['options'][correct_idx]}"
        )

    # Удаляем состояние викторины
    del USER_STATES[user_id]["waiting_for"]
    await callback.answer()


@dp.message(Command("remind"))
async def cmd_remind(message: types.Message):
    try:
        args = message.text.split(maxsplit=2)
        time_str = args[1]
        text = args[2]
    except IndexError:
        await message.answer("Использование: /remind [время] [текст напоминания]\nПример: /remind 15:30 Позвонить маме")
        return

    try:
        # Парсим время напоминания
        now = datetime.now()
        remind_time = datetime.strptime(time_str, "%H:%M").replace(
            year=now.year,
            month=now.month,
            day=now.day
        )

        # Если указанное время уже прошло сегодня, переносим на завтра
        if remind_time < now:
            remind_time += timedelta(days=1)

        if await ReminderManager.set_reminder(message.from_user.id, text, remind_time):
            await message.answer(
                f"⏰ Напоминание установлено на {remind_time.strftime('%Y-%m-%d %H:%M')}:\n"
                f"{text}"
            )
        else:
            await message.answer("Не удалось установить напоминание. Попробуйте позже.")
    except ValueError:
        await message.answer("Неверный формат времени. Используйте ЧЧ:ММ, например 15:30")


@dp.message(Command("who"))
async def cmd_who(message: types.Message):
    try:
        text = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.answer("Использование: /who [текст]\nПример: /who должен вынести мусор")
        return

    # Получаем список участников чата
    chat_members = await bot.get_chat_administrators(message.chat.id)
    if not chat_members:
        await message.answer("Не удалось получить список участников чата.")
        return

    # Выбираем случайного участника
    chosen_member = random.choice(chat_members).user

    # Формируем ответ
    responses = [
        f"🎲 {chosen_member.first_name}, {text}",
        f"✨ По жребию выпало: {chosen_member.first_name}, {text}",
        f"🔮 Магический шар говорит: {chosen_member.first_name}, {text}",
        f"🤔 Думаю, что {chosen_member.first_name} должен(а) {text}",
        f"👑 Корона достается {chosen_member.first_name}, {text}",
    ]

    await message.answer(random.choice(responses))


@dp.message(Command("real_life"))
async def cmd_real_life(message: types.Message):
    response = """
📱 Инструкция по взаимодействию с устройством и приложениями:

1. **Будильник**: Установите будильник на вашем устройстве (смартфон, часы).
2. **Музыка**: Включите любимую песню или плейлист через музыкальный сервис (Яндекс Музыка, Spotify).
3. **Напоминания**: Настройте напоминание в календаре или специальных приложениях.
4. **Погода**: Проверьте погоду через приложение на смартфоне или голосовые помощники.
5. **Переводы**: Используйте Google Translate или аналогичные сервисы для перевода текста.
6. **Умный дом**: Управляйте освещением, климатом и техникой через приложения типа Яндекс.Дома или MiHome.
7. **Расписание**: Создавайте заметки или расписание встреч через календарь.
8. **Карты**: Используйте Google Maps или Яндекс.Карты для навигации.
9. **Здоровье**: Отслеживайте активность и сон через приложения здоровья.
10. **Безопасность**: Настройте резервное копирование данных и двухфакторную аутентификацию.
"""
    await message.answer(response)


@dp.message(Command("statistics"))
async def cmd_statistics(message: types.Message):
    # Статистика по сообщениям
    top_users = sorted(
        users.items(),
        key=lambda x: x[1].get("messages_count", 0),
        reverse=True
    )[:5]

    # Статистика по пайкоинам
    top_rich = sorted(
        users.items(),
        key=lambda x: x[1].get("irisky", 0),
        reverse=True
    )[:5]

    # Общая статистика
    total_users = len(users)
    total_messages = sum(user.get("messages_count", 0) for user in users.values())
    total_irisky = sum(user.get("irisky", 0) for user in users.values())

    # Формируем ответ
    stats_text = (
        f"📊 {bold('Статистика чата')}\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"📨 Всего сообщений: {total_messages}\n"
        f"💰 Всего пайкоинов в системе: {total_irisky}\n\n"
        f"🏆 {bold('Топ активных пользователей:')}\n"
    )

    for idx, (uid, data) in enumerate(top_users, 1):
        stats_text += f"{idx}. {data.get('username', uid)} - {data.get('messages_count', 0)} сообщений\n"

    stats_text += f"\n💰 {bold('Топ богачей:')}\n"
    for idx, (uid, data) in enumerate(top_rich, 1):
        stats_text += f"{idx}. {data.get('username', uid)} - {data.get('irisky', 0)} пайкоинов\n"

    await message.answer(stats_text, parse_mode=ParseMode.HTML)


@dp.message(Command("game_chess"))
async def game_chess(message: types.Message):
    gid = str(message.chat.id)
    if gid not in GAME_STATES:
        # Создаем игру с реальными именами игроков
        white_player = message.from_user.full_name
        black_player = "AI"  # Можно реализовать поиск второго игрока
        game = ChessGame(white_player, black_player)
        GAME_STATES[gid] = game

        # Отправляем начальную доску
        await draw_board_and_send(message.chat.id, game.board)

        await message.answer(
            f"♟ {bold('Новая шахматная партия!')}\n\n"
            f"⚪ Белые: {white_player}\n"
            f"⚫ Чёрные: {black_player}\n\n"
            f"Сейчас ходят: {bold(game.current_turn.capitalize())}\n"
            f"Используйте /move [ход] чтобы сделать ход\n"
            f"Например: /move e2e4",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "⚠ Игра уже идет! Завершите текущую партию командой /end_chess или сделайте ход.\n"
            f"Текущий ход: {GAME_STATES[gid].current_turn.capitalize()}"
        )


@dp.message(F.text.lower() == "🎮 игры")
async def games_button(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♟ Шахматы", callback_data="game_chess")],
            [InlineKeyboardButton(text="🔴 Шашки", callback_data="game_checkers")],
        ]
    )
    await message.answer("Выберите игру:", reply_markup=keyboard)


@dp.callback_query(F.data == "game_chess")
async def game_chess_callback(callback: types.CallbackQuery):
    await callback.message.delete()
    await game_chess(callback.message)


async def draw_board_and_send(chat_id: int, board: chess.Board, orientation: chess.Color = chess.WHITE) -> None:
    """Генерация и отправка шахматной доски"""
    try:
        # Генерируем SVG
        svg_data = chess.svg.board(
            board=board,
            orientation=orientation,
            size=400,
            lastmove=board.peek() if board.move_stack else None,
            check=board.king(chess.WHITE if board.turn == chess.WHITE else chess.BLACK) if board.is_check() else None,
        )

        # Конвертируем SVG в PNG
        drawing = svg2rlg(BytesIO(svg_data.encode("utf-8")))
        png_image = renderPM.drawToString(drawing, fmt="PNG")

        # Создаем объект фото
        photo = BufferedInputFile(png_image, filename="chess_board.png")

        # Отправляем изображение
        await bot.send_photo(chat_id, photo=photo)
    except Exception as e:
        logger.error(f"Ошибка при генерации доски: {e}")
        await bot.send_message(chat_id, "Не удалось сгенерировать изображение доски.")


@dp.message(Command("move"))
async def handle_move(message: types.Message):
    gid = str(message.chat.id)
    if gid not in GAME_STATES or not isinstance(GAME_STATES[gid], ChessGame):
        await message.answer("Сначала начните игру командой /game_chess")
        return

    try:
        move_str = message.text.split(maxsplit=1)[1].strip()
    except IndexError:
        await message.answer("Укажите ход: /move [ход]\nПример: /move e2e4")
        return

    game = GAME_STATES[gid]

    # Проверяем, чей сейчас ход
    current_player = game.white_player if game.current_turn == "white" else game.black_player
    if message.from_user.full_name != current_player and current_player != "AI":
        await message.answer(f"Сейчас не ваш ход. Ожидается ход от {current_player}.")
        return

    if game.move(move_str):
        # После успешного хода обновляем доску
        await draw_board_and_send(
            message.chat.id,
            game.board,
            chess.WHITE if game.current_turn == "white" else chess.BLACK
        )

        # Проверяем окончание игры
        winner = game.winner()
        if winner:
            status = game.get_game_status()
            duration = game.get_game_duration()

            if winner == "Draw":
                result_text = f"🎉 {bold('Ничья!')}\n{status}\nПродолжительность игры: {duration}"
            else:
                result_text = (
                    f"🎉 {bold('Игра окончена!')}\n"
                    f"Победитель: {bold(winner)}\n"
                    f"{status}\n"
                    f"Продолжительность игры: {duration}"
                )

                # Награждаем победителя, если это не AI
                if winner != "AI":
                    uid = next((uid for uid, data in users.items() if data.get("username") == winner), None)
                    if uid:
                        reward = random.randint(20, 50)
                        await IriskyEconomy.add_irisky(int(uid), reward, "Победа в шахматах")
                        result_text += f"\n\n🏆 {winner} получает {reward} пайкоинов за победу!"

            await message.answer(result_text, parse_mode=ParseMode.HTML)
            del GAME_STATES[gid]
        else:
            status = game.get_game_status()
            await message.answer(
                f"♟ Ход {code(move_str)} выполнен!\n\n"
                f"Сейчас ходят: {bold(game.current_turn.capitalize())}\n"
                f"Статус: {status}",
                parse_mode=ParseMode.HTML
            )
    else:
        await message.answer(
            f"❌ Недопустимый ход: {code(move_str)}\n"
            f"Попробуйте другой ход или используйте /help для справки.",
            parse_mode=ParseMode.HTML
        )


@dp.message(Command("end_chess"))
async def end_chess(message: types.Message):
    gid = str(message.chat.id)
    if gid in GAME_STATES and isinstance(GAME_STATES[gid], ChessGame):
        game = GAME_STATES[gid]
        duration = game.get_game_duration()
        del GAME_STATES[gid]
        await message.answer(
            f"🏁 Игра прервана. Доска очищена.\n"
            f"Продолжительность игры: {duration}"
        )
    else:
        await message.answer("Активной шахматной игры не найдено.")


@dp.message(Command("game_checkers"))
async def game_checkers(message: types.Message):
    gid = str(message.chat.id)
    if gid not in GAME_STATES:
        # Создаем игру с реальными именами игроков
        player1 = message.from_user.full_name
        player2 = "AI"  # Можно реализовать поиск второго игрока
        game = CheckersGame(player1, player2)
        GAME_STATES[gid] = game

        await message.answer(
            f"🔴 {bold('Новая игра в шашки!')}\n\n"
            f"🔘 Игрок 1: {player1}\n"
            f"🔴 Игрок 2: {player2}\n\n"
            f"Сейчас ходит: {bold(game.current_player)}\n"
            f"Используйте /move_checkers [откуда] [куда] чтобы сделать ход\n"
            f"Например: /move_checkers 52 43\n\n"
            f"{code(game.show_board())}",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "⚠ Игра уже идет! Завершите текущую партию командой /end_checkers или сделайте ход.\n"
            f"Текущий ход: {GAME_STATES[gid].current_player}"
        )


@dp.message(Command("move_checkers"))
async def handle_checkers_move(message: types.Message):
    gid = str(message.chat.id)
    if gid not in GAME_STATES or not isinstance(GAME_STATES[gid], CheckersGame):
        await message.answer("Сначала начните игру командой /game_checkers")
        return

    try:
        args = message.text.split()
        from_pos = args[1]
        to_pos = args[2]
    except IndexError:
        await message.answer("Укажите ход: /move_checkers [откуда] [куда]\nПример: /move_checkers 52 43")
        return

    game = GAME_STATES[gid]

    # Проверяем, чей сейчас ход
    if message.from_user.full_name != game.current_player and game.current_player != "AI":
        await message.answer(f"Сейчас не ваш ход. Ожидается ход от {game.current_player}.")
        return

    if game.move(from_pos, to_pos):
        # Проверяем окончание игры
        winner = game.winner()
        if winner:
            if winner == "Draw":
                result_text = "🎉 Ничья!"
            else:
                result_text = f"🎉 Победитель: {bold(winner)}!"

                # Награждаем победителя, если это не AI
                if winner != "AI":
                    uid = next((uid for uid, data in users.items() if data.get("username") == winner), None)
                    if uid:
                        reward = random.randint(15, 40)
                        await IriskyEconomy.add_irisky(int(uid), reward, "Победа в шашках")
                        result_text += f"\n\n🏆 {winner} получает {reward} пайкоинов за победу!"

            await message.answer(
                f"{result_text}\n\n"
                f"Итоговая доска:\n\n"
                f"{code(game.show_board())}",
                parse_mode=ParseMode.HTML
            )
            del GAME_STATES[gid]
        else:
            await message.answer(
                f"🔴 Ход {from_pos}-{to_pos} выполнен!\n\n"
                f"Сейчас ходит: {bold(game.current_player)}\n\n"
                f"{code(game.show_board())}",
                parse_mode=ParseMode.HTML
            )
    else:
        await message.answer(
            f"❌ Недопустимый ход: {from_pos}-{to_pos}\n"
            f"Попробуйте другой ход или используйте /help для справки.",
            parse_mode=ParseMode.HTML
        )


@dp.message(Command("end_checkers"))
async def end_checkers(message: types.Message):
    gid = str(message.chat.id)
    if gid in GAME_STATES and isinstance(GAME_STATES[gid], CheckersGame):
        game = GAME_STATES[gid]
        del GAME_STATES[gid]
        await message.answer(
            f"🏁 Игра прервана. Доска очищена.\n\n"
            f"Итоговая доска:\n\n"
            f"{code(game.show_board())}",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("Активной игры в шашки не найдено.")


@dp.callback_query(F.data == "game_checkers")
async def game_checkers_callback(callback: types.CallbackQuery):
    await callback.message.delete()
    await game_checkers(callback.message)


# ================== МОДЕРАЦИОННЫЕ КОМАНДЫ ==================

@dp.message(Command("warn"))
async def cmd_warn(message: types.Message):
    uid = str(message.from_user.id)
    if not users.get(uid, {}).get("is_moderator", False):
        await message.answer("❌ У вас нет прав для выдачи предупреждений.")
        return

    try:
        args = message.text.split(maxsplit=2)
        warn_id = args[1]
        reason = args[2] if len(args) > 2 else "Нарушение правил чата"
    except IndexError:
        await message.answer("Использование: /warn [ID] [причина]")
        return

    if warn_id not in users:
        await message.answer("Пользователь с указанным ID не найден.")
        return

    users[warn_id]["warnings"].append({
        "timestamp": datetime.now().isoformat(),
        "reason": reason,
        "moderator": users[uid]["username"],
    })

    warn_count = len(users[warn_id]["warnings"])

    # Если 3 или более предупреждений - бан на 24 часа
    if warn_count >= 3:
        users[warn_id]["ban_expiry"] = (datetime.now() + timedelta(hours=24)).isoformat()
        await message.answer(
            f"⚠ Пользователь {users[warn_id]['username']} получил предупреждение ({warn_count}/3).\n"
            f"Причина: {reason}\n\n"
            f"🚫 Пользователь заблокирован на 24 часа за 3 предупреждения."
        )
    else:
        await message.answer(
            f"⚠ Пользователь {users[warn_id]['username']} получил предупреждение ({warn_count}/3).\n"
            f"Причина: {reason}"
        )

    save_data()


@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    uid = str(message.from_user.id)
    if not users.get(uid, {}).get("is_moderator", False):
        await message.answer("❌ У вас нет прав для блокировки пользователей.")
        return

    try:
        args = message.text.split(maxsplit=2)
        ban_id = args[1]
        reason = args[2] if len(args) > 2 else "Нарушение правил чата"
    except IndexError:
        await message.answer("Использование: /ban [ID] [причина]")
        return

    if ban_id not in users:
        await message.answer("Пользователь с указанным ID не найден.")
        return

    users[ban_id]["ban_expiry"] = (datetime.now() + timedelta(hours=24)).isoformat()
    await message.answer(
        f"🚫 Пользователь {users[ban_id]['username']} заблокирован на 24 часа.\n"
        f"Причина: {reason}"
    )
    save_data()


@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    uid = str(message.from_user.id)
    if not users.get(uid, {}).get("is_moderator", False):
        await message.answer("❌ У вас нет прав для разблокировки пользователей.")
        return

    try:
        unban_id = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.answer("Использование: /unban [ID]")
        return

    if unban_id not in users:
        await message.answer("Пользователь с указанным ID не найден.")
        return

    users[unban_id]["ban_expiry"] = None
    users[unban_id]["warnings"] = []  # Снимаем все предупреждения
    await message.answer(f"✅ Пользователь {users[unban_id]['username']} разблокирован.")
    save_data()


@dp.message(Command("clearwarns"))
async def cmd_clearwarns(message: types.Message):
    uid = str(message.from_user.id)
    if not users.get(uid, {}).get("is_moderator", False):
        await message.answer("❌ У вас нет прав для снятия предупреждений.")
        return

    try:
        clear_id = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.answer("Использование: /clearwarns [ID]")
        return

    if clear_id not in users:
        await message.answer("Пользователь с указанным ID не найден.")
        return

    users[clear_id]["warnings"] = []
    await message.answer(f"✅ Все предупреждения пользователя {users[clear_id]['username']} сняты.")
    save_data()


# ================== ОБРАБОТКА ОШИБОК ==================

@dp.errors()
async def errors_handler(update: types.Update, exception: Exception):
    logger.error(f"Ошибка при обработке запроса: {exception}", exc_info=True)

    try:
        if isinstance(update, types.Message):
            await update.answer(
                "⚠ Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже."
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")


# ================== ЗАПУСК БОТА ==================

async def on_startup():
    logger.info("Бот запущен")
    # Запускаем фоновую задачу для проверки напоминаний
    asyncio.create_task(check_reminders_background())


async def check_reminders_background():
    """Фоновая задача для проверки и отправки напоминаний"""
    while True:
        try:
            await ReminderManager.check_reminders()
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче проверки напоминаний: {e}")
        await asyncio.sleep(60)  # Проверяем каждую минуту


async def on_shutdown():
    logger.info("Бот остановлен")
    save_data()


async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запускаем бота
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
