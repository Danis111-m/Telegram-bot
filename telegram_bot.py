import aiohttp
import asyncio
import glob
import random
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from io import BytesIO
from random import randint, choice
import os
from datetime import datetime, timedelta

from PIL import ImageFont, ImageDraw, Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ApplicationBuilder, \
    CallbackQueryHandler, \
    CallbackContext
import requests
import json

bot_responses = {}

GROUP_ID = "-4"

with open("po.json", "r") as f:
    config = json.load(f)
    BOT_TOKEN = config["BOT_KEY"]
    WEATHER_API_KEY = config["WEATHER_API_KEY"]
    RUNWARE_API_KEY = config["RUNWARE_API_KEY"]

# Создаем объекты кнопок
button1 = InlineKeyboardButton(text="Нажми меня!", callback_data="button1")
button2 = InlineKeyboardButton(text="Другая кнопка", callback_data="button2")
button_url = InlineKeyboardButton("Ссылка на Путина",
                                  url="https://ru.wikipedia.org/wiki/%D0%92%D0%BB%D0%B0%D0%B4%D0%B8%D0%BC%D0%B8%D1%80_(%D0%B3%D0%BE%D1%80%D0%BE%D0%B4,_%D0%A0%D0%BE%D1%81%D1%81%D0%B8%D1%8F)",
                                  callback_data="putin")
keyboard = [
    [button1, button2],  # Первый ряд с двумя кнопками
    [InlineKeyboardButton(text="Ссылка на Путина",
                                  url="https://ru.wikipedia.org/wiki/%D0%92%D0%BB%D0%B0%D0%B4%D0%B8%D0%BC%D0%B8%D1%80_(%D0%B3%D0%BE%D1%80%D0%BE%D0%B4,_%D0%A0%D0%BE%D1%81%D1%81%D0%B8%D1%8F)",
                                  callback_data="putin")]  # Второй ряд с одной кнопкой
]
reply_markup = InlineKeyboardMarkup(keyboard)

rps_keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("✊ Камень", callback_data="rps_камень"),
        InlineKeyboardButton("✋ Бумага", callback_data="rps_бумага"),
        InlineKeyboardButton("✌️ Ножницы", callback_data="rps_ножницы")
    ]
])

YOUTUBE_URL_PATTERN = re.compile(
    r"((?:https?://)?(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/\S+)",
    re.IGNORECASE
)
YOUTUBE_FORMAT_CANDIDATES = [
    "bestvideo[ext=mp4]/bestvideo",
    "bv*[ext=mp4]/bv*",
    "best/bestvideo",
]
youtube_dl = None


def extract_youtube_url(text: str):
    match = YOUTUBE_URL_PATTERN.search(text or "")
    if not match:
        return None
    url = match.group(1).rstrip(".,!?)]}>\"'")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _build_ydl_options(download_dir: str, format_selector: str):
    return {
        "format": format_selector,
        "outtmpl": os.path.join(download_dir, "%(title).80s-%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "concurrent_fragment_downloads": 4,
        # YouTube periodically changes available clients. We try a wider set.
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv", "web"]}},
    }


def _get_youtube_dl_module():
    global youtube_dl
    if youtube_dl is not None:
        return youtube_dl

    try:
        import yt_dlp as ytdl_module
    except ImportError:
        import youtube_dl as ytdl_module

    youtube_dl = ytdl_module
    return youtube_dl


def _resolve_ytdlp_cli_path():
    local_venv_cli = os.path.join(os.getcwd(), ".venv", "Scripts", "yt-dlp.exe")
    if os.path.exists(local_venv_cli):
        return local_venv_cli

    py_dir_cli = os.path.join(os.path.dirname(sys.executable), "yt-dlp.exe")
    if os.path.exists(py_dir_cli):
        return py_dir_cli

    return shutil.which("yt-dlp")


def _latest_downloaded_file(download_dir: str):
    candidates = [
        path for path in glob.glob(os.path.join(download_dir, "*"))
        if os.path.isfile(path)
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _download_youtube_video_cli(url: str, download_dir: str):
    cli_path = _resolve_ytdlp_cli_path()
    if not cli_path:
        raise RuntimeError("yt-dlp CLI не найден.")

    outtmpl = os.path.join(download_dir, "%(title).80s-%(id)s.%(ext)s")
    errors = []

    for format_selector in YOUTUBE_FORMAT_CANDIDATES:
        command = [
            cli_path,
            "-f", format_selector,
            "--no-playlist",
            "--restrict-filenames",
            "--retries", "5",
            "--fragment-retries", "5",
            "--socket-timeout", "30",
            "--concurrent-fragments", "4",
            "--merge-output-format", "mp4",
            "--print", "after_move:filepath",
            "-o", outtmpl,
            url,
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=1200,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e:
            errors.append(f"{format_selector}: {e}")
            continue

        if result.returncode == 0:
            printed_paths = [
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and os.path.exists(line.strip())
            ]
            file_path = printed_paths[-1] if printed_paths else _latest_downloaded_file(download_dir)
            if not file_path:
                errors.append(f"{format_selector}: файл не найден после скачивания")
                continue

            title = os.path.splitext(os.path.basename(file_path))[0]
            return file_path, title

        stderr_tail = "\n".join([line for line in result.stderr.splitlines() if line.strip()][-3:])
        errors.append(f"{format_selector}: {stderr_tail or 'ошибка yt-dlp CLI'}")

    joined_errors = "\n".join(errors[-2:]) if errors else "Неизвестная ошибка."
    raise RuntimeError(f"yt-dlp CLI не смог скачать видео.\n{joined_errors}")


def _download_youtube_video(url: str, download_dir: str):
    errors = []

    try:
        return _download_youtube_video_cli(url, download_dir)
    except Exception as e:
        errors.append(f"CLI: {e}")

    ytdl_module = _get_youtube_dl_module()
    for format_selector in YOUTUBE_FORMAT_CANDIDATES:
        ydl_opts = _build_ydl_options(download_dir, format_selector)
        try:
            with ytdl_module.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise RuntimeError("Не удалось получить данные о видео.")

                if isinstance(info, dict) and info.get("entries"):
                    info = info["entries"][0]
                    if not info:
                        raise RuntimeError("Пустой плейлист или видео недоступно.")

                title = info.get("title") or "YouTube video"
                file_path = info.get("_filename") or info.get("filepath")

                requested_downloads = info.get("requested_downloads")
                if not file_path and isinstance(requested_downloads, list) and requested_downloads:
                    file_path = requested_downloads[0].get("filepath")

                if not file_path:
                    file_path = ydl.prepare_filename(info)

                if not os.path.exists(file_path):
                    base, _ = os.path.splitext(file_path)
                    candidates = glob.glob(base + ".*")
                    if not candidates:
                        raise FileNotFoundError("Не удалось найти скачанный файл.")
                    file_path = max(candidates, key=os.path.getmtime)

                return file_path, title
        except Exception as e:
            errors.append(f"Python API {format_selector}: {e}")

    joined_errors = "\n".join(errors[-2:]) if errors else "Неизвестная ошибка."
    raise RuntimeError(f"Не удалось скачать видео ни в одном формате.\n{joined_errors}")


async def download_and_send_youtube_video(update: Update, url: str):
    status_message = await update.message.reply_text("Скачиваю видео с YouTube, подожди...")
    try:
        with tempfile.TemporaryDirectory() as download_dir:
            file_path, title = await asyncio.to_thread(_download_youtube_video, url, download_dir)
            with open(file_path, "rb") as video_file:
                await update.message.reply_document(
                    document=video_file,
                    filename=os.path.basename(file_path),
                    caption=f"{title}\nИсточник: {url}"[:1024]
                )
        try:
            await status_message.delete()
        except Exception:
            pass
    except Exception as e:
        error_text = str(e)
        if "file is too big" in error_text.lower():
            error_message = (
                "Видео скачано, но Telegram не дал отправить файл из-за ограничения размера."
            )
        elif "requested format is not available" in error_text.lower():
            error_message = (
                "YouTube не отдал подходящий поток для этого видео. "
                "Попробуй другую ссылку или повтори позже."
            )
        else:
            error_message = f"Не удалось скачать/отправить видео: {e}"

        try:
            await status_message.edit_text(error_message)
        except Exception:
            await update.message.reply_text(error_message)


async def yt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /yt <ссылка на YouTube>")
        return

    raw_text = " ".join(context.args).strip()
    youtube_url = extract_youtube_url(raw_text)
    if not youtube_url:
        await update.message.reply_text("Не вижу корректной ссылки YouTube.")
        return

    await download_and_send_youtube_video(update, youtube_url)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(update.effective_user.first_name)
    print("Получена команда /start")  # Добавляем отладку
    await update.message.reply_text("Введите команду /comand чтобы ознакомиться с командами.")
    await update.message.reply_text('Привет! Я тестовый бот. Напиши что-нибудь!', reply_markup=reply_markup)


async def echo(update: Update, context):
    if not update.message or not update.message.text:
        return

    word_user = update.message.text
    youtube_url = extract_youtube_url(word_user)
    if youtube_url:
        await download_and_send_youtube_video(update, youtube_url)
        return

    word_reserve = word_user[::-1]
    name = update.effective_user.name
    await update.message.reply_text(f'Ты написал: {word_reserve}, отправитель: {name}')
    #await update.message.delete()
    chat = update.effective_chat
    chat_info = f"""
    Chat ID: {chat.id}
    Title: {chat.title or "—"}
    First name: {chat.first_name or update.message.from_user.first_name or "-"}
    Last name: {chat.last_name or "—" or update.message.from_user.last_name or "-"}
    Username: @{chat.username or update.message.from_user.username or "—"}
    Type: {chat.type}
    Дата сообщения: {update.message.date}
        """.strip()

    await update.message.reply_text(chat_info)
async def send_message_to_group(update: Update, context: CallbackContext):
    if not context.args:
        await update.message.reply_text("Укажите сообщение для письма")
        return

    message_text = ' '.join(context.args)
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=message_text
    )


async def guess_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("number"):
        print(context.user_data)
    else:
        context.user_data["number"] = str(random.randint(1, 10))

    if context.args[0] > context.user_data["number"]:
        await update.message.reply_text("Вы не угадали, число меньше")
    elif context.args[0] < context.user_data["number"]:
        await update.message.reply_text("Вы не угадали, число больше")
    else:
        await update.message.reply_text("Поздравляю вы угадали, число сброшено")
        context.user_data["number"] = str(random.randint(1, 10))


async def settimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /settimer <секунды>")

    seconds = int(context.args[0])
    print(seconds)
    await update.message.reply_text(f"Таймер на {seconds} установлен!")
    await asyncio.sleep(seconds)

    await update.message.reply_text("Таймер сработал!")


async def settimer_job_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /settimer <секунды>")

    seconds = int(context.args[0])
    print(seconds)
    await update.message.reply_text(f"Таймер на {seconds} установлен!")
    chat_id = update.effective_chat.id
    context.job_queue.run_once(print_timer, seconds, chat_id=chat_id)


async def print_timer(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    await context.bot.send_message(chat_id=job.chat_id, text="Таймер сработал!")


async def print_random_number(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    await context.bot.send_message(chat_id=job.chat_id, text=f"Число {random.randint(1, 11)}!")


async def start_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.chat_data.get("spam_command"):
        await update.message.reply_text("Команда уже запущена")
        return
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /start_spam <секунды>")
    seconds = int(context.args[0])
    context.chat_data["spam_command"] = context.job
    print(seconds)
    await update.message.reply_text(f"Бомбер на интервал: {seconds} установлен!")
    chat_id = update.effective_chat.id
    context.job_queue.run_repeating(print_random_number, interval=seconds, chat_id=chat_id)


async def get_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /getWeather <название города>")
        return

    city = context.args[0]
    print(city)

    url = "http://api.weatherapi.com/v1/current.json"
    params = {
        'key': WEATHER_API_KEY,  # Используйте 'key' вместо 'appid'
        'q': city,
        'aqi': 'yes'  # Если вам нужно, добавьте параметр для получения данных о качестве воздуха
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()  # Проверка на ошибки HTTP

                data = await response.json()
                message = f"""
                Информация о погоде.
                Город: {data['location']['name']},
                Регион: {data['location']['region']},
                Текущее время: {data['location']['localtime']},
                Температура в градусах: {data['current']['temp_c']}°C,
                Облачность: {data['current']['condition']['text']},
                Влажность: {data['current']['humidity']}%,
                Скорость ветра (км/ч): {data['current']['wind_kph']}
                """
                print(data)
                print(message)
                await update.message.reply_text(message)
    except Exception as e:
        print(f'Ошибка: {e}')
        await update.message.reply_text(f"Ошибка: {e}")

async def start_timer_weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.chat_data.get("timer_weather_command"):
        await update.message.reply_text("Команда уже запущена")
        return
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /startGetWeather <название города>")
        return
    city = context.args[0]
    await update.message.reply_text(f"Таймер на получение погоды города: {city} установлен!")
    context.chat_data["timer_weather_command"] = context.job
    context.job.data['city'] = city
    chat_id = update.effective_chat.id
    context.job_queue.run_repeating(get_weather_callback, 10, chat_id=chat_id, data=context.job.data)

async def get_weather_callback(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    data = job.data  # Используйте job.data вместо context.data

    url = 'http://api.weatherapi.com/v1/current.json'
    params = {
        'key': WEATHER_API_KEY,
        'q': data['city'],
        'aqi': 'yes'
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()

                data = await response.json()
                message = f"""
                Информация о погоде.
                Город: {data['location']['name']},
                Регион: {data['location']['region']},
                Текущее время: {data['location']['localtime']},
                Температура в градусах: {data['current']['temp_c']}°C,
                Облачность: {data['current']['condition']['text']},
                Влажность: {data['current']['humidity']}%,
                Скорость ветра (км/ч): {data['current']['wind_kph']}
                """
                print(data)
                print(message)
                await context.bot.send_message(chat_id=job.chat_id, text=message)
    except Exception as e:
        print(f'Ошибка: {e}')
        await context.bot.send_message(chat_id=job.chat_id, text=f"Ошибка: {e}")

# Команда /startmagnitogorsk
async def start_magnitogorsk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if context.chat_data.get("magnitogorsk_job"):
        await update.message.reply_text(
            "Погода Магнитогорска уже отправляется! Используйте /stopmagnitogorsk, чтобы остановить.")
        return

    job = context.job_queue.run_repeating(
        magnitogorsk_weather_callback,
        interval=10,
        first=0,
        chat_id=chat_id
    )

    context.chat_data["magnitogorsk_job"] = job

    await update.message.reply_text(
        "Началась отправка погоды в Магнитогорске каждые 10 секунд! Остановить: /stopmagnitogorsk")

async def magnitogorsk_weather_callback(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    url = f"http://api.weatherapi.com/v1/current.json"
    params = {
        "q": "Magnitogorsk",
        "key": WEATHER_API_KEY,
        "aqi": "no"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()

                data = await response.json()
                temp = data['current']['temp_c']
                condition = data['current']['condition']['text']
                feels_like = data['current']['feelslike_c']
                humidity = data['current']['humidity']

                message = (
                    f"Погода в Магнитогорске:\n"
                    f"Описание: {condition}\n"
                    f"Температура: {temp}°C\n"
                    f"Ощущается как: {feels_like}°C\n"
                    f"Влажность: {humidity}%"
                )
                await context.bot.send_message(chat_id=chat_id, text=message)

    except Exception as e:
        print(e)
        await context.bot.send_message(chat_id=chat_id, text="Ошибка при получении погоды в Магнитогорске.")

async def stop_magnitogorsk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.chat_data.get("magnitogorsk_job"):
        context.chat_data["magnitogorsk_job"].schedule_removal()
        del context.chat_data["magnitogorsk_job"]  # Удалите задачу из chat_data
    await update.message.reply_text("Погода прекращена!")


async def get_astronomy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /getAstro <название города>")
        return

    city = context.args[0]
    print(city)

    url = 'http://api.weatherapi.com/v1/astronomy.json'
    api_key = '935ddbb4cc864af4b64175558251206'  # Убедитесь, что ваш API-ключ действителен
    dt = '2025-06-12'  # Вы можете изменить дату на текущую или переданную пользователем

    params = {
        'key': api_key,
        'q': city,
        'dt': dt
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()  # Проверка на ошибки HTTP

                data = await response.json()
                message = f"""
                Астрономическая информация:
                Город: {data['location']['name']},
                Регион: {data['location']['region']},
                Текущее время: {data['location']['localtime']},
                Время: {data['location']['localtime_epoch']},
                Страна: {data['location']['country']},
                Восход солнца: {data['astronomy']['astro']['sunrise']}.
                Заход солнца: {data['astronomy']['astro']['sunset']},
                Восход луны: {data['astronomy']['astro']['moonrise']},
                Заход луны: {data['astronomy']['astro']['moonset']},
                Луна взошла: {data['astronomy']['astro']['is_moon_up']},
                Солнце взошло: {data['astronomy']['astro']['is_sun_up']}
                """
                print(data)
                print(message)
                await update.message.reply_text(message)

    except Exception as e:
        print(f'Ошибка: {e}')
        await update.message.reply_text(f"Ошибка: {e}")


async def start_timer_astronomy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.chat_data.get("timer_astronomy_command"):
        await update.message.reply_text("Команда уже запущена")
        return
    if not context.args:
        await update.message.reply_text("Вы должны ввести команду в формате /startGetAstro <название города>")
        return
    city = context.args[0]
    await update.message.reply_text(f"Таймер на получение астрономии города: {city} установлен!")
    chat_id = update.effective_chat.id
    job = context.job_queue.run_repeating(
        get_astronomy_callback,
        interval=10,
        chat_id=chat_id,
        data={"city": city}
    )
    context.chat_data["timer_astronomy_command"] = job


async def get_astronomy_callback(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    city = (job.data or {}).get("city", "Magnitogorsk")
    dt = datetime.now().strftime("%Y-%m-%d")

    url = 'http://api.weatherapi.com/v1/astronomy.json'

    params = {
        'key': WEATHER_API_KEY,
        'q': city,
        'dt': dt
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

        message = f"""
            Астрономическая информация:
            Город: {data['location']['name']},
            Регион: {data['location']['region']},
            Текущее время: {data['location']['localtime']},
            Страна: {data['location']['country']},
            Восход солнца: {data['astronomy']['astro']['sunrise']},
            Заход солнца: {data['astronomy']['astro']['sunset']},
            Восход луны: {data['astronomy']['astro']['moonrise']},
            Заход луны: {data['astronomy']['astro']['moonset']},
            Луна взошла: {data['astronomy']['astro']['is_moon_up']},
            Солнце взошло: {data['astronomy']['astro']['is_sun_up']}
            """
        print(data)
        print(message)
        await context.bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        print(f'Ошибка:{e}')
        await context.bot.send_message(chat_id=chat_id, text=f"{e}")


async def play_rpc(update: Update, context):
    await update.message.reply_text("Начинаем игру в цуэ-фа!", reply_markup=rps_keyboard)

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /generate_image <размер> <текст>")
        return

    try:
        size = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Размер должен быть целым числом.")
        return

    if size < 64 or size > 2048:
        await update.message.reply_text("Размер должен быть в диапазоне от 64 до 2048.")
        return

    text = " ".join(context.args[1:]).strip()
    if not text:
        await update.message.reply_text("Добавьте текст для изображения.")
        return

    try:
        image = Image.new('RGB', (size, size), color=(173, 216, 230))
        draw = ImageDraw.Draw(image)

        try:
            font = ImageFont.truetype("arial.ttf", max(size // 10, 12))
        except OSError:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        position = ((size - text_width) // 2, (size - text_height) // 2)

        draw.text(position, text, fill="black", font=font)

        buffer = BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)

        await update.message.reply_photo(photo=buffer, caption="Вот ваше изображение!")

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

RUNWARE_API_URL = "https://api.runware.ai/v1"


def get_runware_image_url(payload: dict, task_types: tuple):
    items = payload.get("data", [])
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("taskType") in task_types and item.get("imageURL"):
            return item.get("imageURL")
    return None


def get_runware_error(payload: dict):
    if not isinstance(payload, dict):
        return "Некорректный ответ сервера."

    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            return error.get("message") or str(error)
        return str(error)

    items = payload.get("data", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_error = item.get("error")
            if item_error:
                if isinstance(item_error, dict):
                    return item_error.get("message") or str(item_error)
                return str(item_error)

    return None


async def generate_image_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /generate_image_ai <описание изображения>")
        return

    if not RUNWARE_API_KEY:
        await update.message.reply_text("RUNWARE_API_KEY не найден в конфигурации.")
        return

    try:
        prompt = " ".join(context.args).strip()
        if not prompt:
            await update.message.reply_text("Опишите изображение после команды.")
            return

        task_uuid = str(uuid.uuid4())

        payload = [
            {
                "taskType": "imageInference",
                "taskUUID": task_uuid,
                "positivePrompt": prompt,
                "model": "civitai:43331@176425",
                "numberResults": 1,
                "negativePrompt": "low quality, blurry, distorted",
                "height": 512,
                "width": 512,
                "outputFormat": "PNG",
                "CFGScale": 7,
                "steps": 30
            }
        ]

        headers = {
            "Authorization": f"Bearer {RUNWARE_API_KEY}",
            "Content-Type": "application/json"
        }

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(RUNWARE_API_URL, json=payload, headers=headers) as response:
                response_text = await response.text()
                if response.status != 200:
                    await update.message.reply_text(f"Ошибка HTTP: {response.status}, {response_text}")
                    return

                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    await update.message.reply_text("Сервис вернул некорректный JSON.")
                    return

                image_url = get_runware_image_url(data, ("imageInference",))
                if not image_url:
                    error_text = get_runware_error(data) or "Сервис не вернул URL изображения."
                    await update.message.reply_text(f"Ошибка генерации: {error_text}")
                    return

            async with session.get(image_url) as image_response:
                if image_response.status != 200:
                    await update.message.reply_text(f"Не удалось скачать изображение: {image_response.status}")
                    return

                image_bytes = await image_response.read()
                await update.message.reply_photo(
                    photo=image_bytes,
                    caption=f"Сгенерировано изображение: {prompt}"
                )
    except Exception as e:
        await update.message.reply_text(f"Ошибка при генерации изображения: {e}")

async def edit_image_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.caption:
        await update.message.reply_text("Вы должны добавить подпись к фото с описанием запроса!")
        return

    if not RUNWARE_API_KEY:
        await update.message.reply_text("RUNWARE_API_KEY не найден в конфигурации.")
        return

    prompt = update.message.caption  # Получаем подпись целиком
    if not update.message.photo:
        await update.message.reply_text("Не удалось получить фото из сообщения.")
        return

    photo = update.message.photo[-1]  # Берем фото с наибольшим разрешением
    file = await context.bot.get_file(photo.file_id)

    image_data = await file.download_as_bytearray()
    import base64
    image_base64 = base64.b64encode(image_data).decode('utf-8')

    task_uuid = str(uuid.uuid4())

    payload = [
        {
            "taskType": "photoMaker",
            "taskUUID": task_uuid,
            "width": 1024,
            "height": 1024,
            "numberResults": 1,
            "outputFormat": "JPEG",
            "steps": 20,
            "CFGScale": 7.5,
            "positivePrompt": prompt,
            "model": "civitai:139562@798204",
            "inputImages": [f"data:image/jpeg;base64,{image_base64}"]  # Добавляем изображение в base64
        }
    ]

    headers = {
        "Authorization": f"Bearer {RUNWARE_API_KEY}",
        "Content-Type": "application/json"
    }

    timeout = aiohttp.ClientTimeout(total=180)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(RUNWARE_API_URL, json=payload, headers=headers) as response:
                response_text = await response.text()
                if response.status != 200:
                    await update.message.reply_text(f"Ошибка HTTP: {response.status}, {response_text}")
                    return

                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    await update.message.reply_text("Сервис вернул некорректный JSON.")
                    return

                image_url = get_runware_image_url(data, ("photoMaker", "imageInference"))
                if not image_url:
                    error_text = get_runware_error(data) or "Сервис не вернул URL изображения."
                    await update.message.reply_text(f"Ошибка редактирования: {error_text}")
                    return

            async with session.get(image_url) as image_response:
                if image_response.status != 200:
                    await update.message.reply_text(f"Не удалось скачать изображение: {image_response.status}")
                    return
                await update.message.reply_photo(photo=await image_response.read())
    except Exception as e:
        await update.message.reply_text(f"Ошибка редактирования изображения: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Подтверждаем получение callback

    if query.data.startswith("rps_"):
        user_choice = query.data.replace("rps_", "")
        bot_choice = choice(["камень", "бумага", "ножницы"])

        beats = {
            "камень": "ножницы",
            "ножницы": "бумага",
            "бумага": "камень"
        }

        if user_choice not in beats:
            await query.message.reply_text("Ошибка выбора. Попробуй еще раз: /play_rpc")
            return

        if user_choice == bot_choice:
            result = "Ничья!"
        elif beats[user_choice] == bot_choice:
            result = "Ты победил!"
        else:
            result = "Бот победил!"

        await query.message.reply_text(
            f"Ты выбрал: {user_choice}\nБот выбрал: {bot_choice}\n\n{result}"
        )
        return

    if query.data == "button1":
        await query.message.reply_text("Вы нажали первую кнопку!")
    elif query.data == "button2":
        await query.message.reply_text("Вы нажали вторую кнопку!")
    elif query.data == "button3":
        await query.message.reply_text("Вы нажали третью кнопку!")

async def comand(update: Update, context: CallbackContext):
    commands = (
        "/start - Начать взаимодействие с ботом\n"
        "/guess - Угадать что-то </guess [число]>\n"
        "/comand - Показать список команд\n"
        "/yt - Скачать видео с YouTube </yt ссылка>\n"
        "/settimer - Таймер на несколько секунд\n"
        "/settimerJOB - Таймер на несколько секунд\n"
        "/start_spam - Бомбер-спам\n"
        "/getWeather - Погода <город на английском>\n"
        "/setTimerWeather - Таймер на получение погоды\n"
        "/startmagnitogorsk - Отправляет прогноз погоды каждые 10 секунд\n"
        "/stopmagnitogorsk - Останавливает прогноз погоды в г.Магнитогорске\n"
        "/getAstro - Астрономический календарь\n"
        "/startGetAstro - Показывает астрономию в г.Магнитогорске\n"
        "/play_rpc - Игра в камень ножницы бумага\n"
        "Отправь ссылку YouTube - бот скачает и отправит видео\n"
        "/generate_image - Генерирует изображение\n"
        "/generate_image_ai - Нейросеть сгенерирует любое изображение\n"
        "/edit_image_ai - Изменит любое изображение\n"
        "/play - Игра 21 (blackjack)\n"
        "/cancel - Отмена текущей игры 21\n"
        "/poll - Опрос\n"
        "/start_makaka - Игра в макакаметр\n"
        "/score - Твои очки в игре макакаметр\n"
        "/reset - Сбрасывает все твои очки в игре макакаметр\n"
        "/discriminant - Посчитает корни дискрименанта\n"
    )
    await update.message.reply_text(commands)

async def poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = "Какой ваш любимый цвет?"
    options = ["🔴 Красный", "🟢 Зелёный", "🔵 Синий"]

    await context.bot.send_poll(
        chat_id=update.effective_chat.id,
        question=question,
        options=options,
        is_anonymous=False,  # Если хочешь знать, кто голосовал
        allows_multiple_answers=False,  # Разрешить один ответ
    )

# Словарь для хранения очков пользователей в памяти
user_scores = {}
user_activities = {}  # Словарь для хранения активности макаки по пользователю

FEED_WINDOW = timedelta(hours=5)
MAX_FEEDS_PER_WINDOW = 2
WALK_COOLDOWN = timedelta(days=1)
FEED_POINTS = 5
WALK_POINTS = 10


def ensure_maka_user(user_id: int):
    if user_id not in user_scores:
        user_scores[user_id] = 0
    if user_id not in user_activities:
        user_activities[user_id] = {"walk": None, "feed_times": []}

    user_activities[user_id].setdefault("walk", None)
    user_activities[user_id].setdefault("feed_times", [])


def get_maka_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Покормить макаку 🍌", callback_data='feed')],
        [InlineKeyboardButton("Погулять с макакой 🚶", callback_data='walk')]
    ])


def format_remaining_time(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


# Команда /start_makaka — начать игру
async def start_maka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_maka_user(user.id)

    await update.message.reply_text(
        (
            f"Привет, {user.first_name}! Это игра Макакаметр.\n"
            f"Правила:\n"
            f"• Кормить можно {MAX_FEEDS_PER_WINDOW} раза за {int(FEED_WINDOW.total_seconds() // 3600)} часов (+{FEED_POINTS} очков).\n"
            f"• Гулять можно 1 раз в 24 часа (+{WALK_POINTS} очков)."
        ),
        reply_markup=get_maka_keyboard()
    )


# Обработка нажатия кнопки "Прогулка"
async def walk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    ensure_maka_user(user_id)
    now = datetime.now()

    last_walk = user_activities[user_id]["walk"]
    if last_walk and now - last_walk < WALK_COOLDOWN:
        remaining = WALK_COOLDOWN - (now - last_walk)
        await query.edit_message_text(
            text=(
                f"Сегодня прогулка уже была.\n"
                f"Следующая прогулка через: {format_remaining_time(remaining)}"
            ),
            reply_markup=get_maka_keyboard()
        )
        return

    user_activities[user_id]["walk"] = now
    user_scores[user_id] += WALK_POINTS
    await query.edit_message_text(
        text=(
            f"Вы погуляли с макакой! +{WALK_POINTS} очков.\n"
            f"Текущий счёт: {user_scores[user_id]} очков."
        ),
        reply_markup=get_maka_keyboard()
    )


# Обработка нажатия кнопки "Покормить"
async def feed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    ensure_maka_user(user_id)
    now = datetime.now()

    feed_times = user_activities[user_id]["feed_times"]
    feed_times = [feed_time for feed_time in feed_times if now - feed_time < FEED_WINDOW]
    user_activities[user_id]["feed_times"] = feed_times

    if len(feed_times) >= MAX_FEEDS_PER_WINDOW:
        next_feed_at = feed_times[0] + FEED_WINDOW
        remaining = next_feed_at - now
        await query.edit_message_text(
            text=(
                f"Лимит кормления: {MAX_FEEDS_PER_WINDOW} раза за 5 часов.\n"
                f"Следующее кормление через: {format_remaining_time(remaining)}"
            ),
            reply_markup=get_maka_keyboard()
        )
        return

    user_activities[user_id]["feed_times"].append(now)
    user_scores[user_id] += FEED_POINTS
    current_window_feeds = len(user_activities[user_id]["feed_times"])

    await query.edit_message_text(
        text=(
            f"Макака покормлена! +{FEED_POINTS} очков.\n"
            f"Кормлений в текущем окне: {current_window_feeds}/{MAX_FEEDS_PER_WINDOW}.\n"
            f"Текущий счёт: {user_scores[user_id]} очков."
        ),
        reply_markup=get_maka_keyboard()
    )

# Команда /score — показать текущий счёт пользователя
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    score = user_scores.get(user_id, 0)
    await update.message.reply_text(f"Твой текущий счёт: {score} очков.")

# Команда /reset — сбросить счёт пользователя
async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_scores[user_id] = 0
    await update.message.reply_text("Твой счёт был сброшен на 0.")


async def discriminant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        await update.message.reply_text("Использование: /discriminant <a> <b> <c>")
        return

    try:
        a = float(context.args[0])
        b = float(context.args[1])
        c = float(context.args[2])

        D = b**2 - 4*a*c  # Расчет дискриминанта

        if D > 0:
            await update.message.reply_text(f"Дискриминант D = {D}. Уравнение имеет два различных действительных корня.")
        elif D == 0:
            await update.message.reply_text(f"Дискриминант D = {D}. Уравнение имеет один действительный корень.")
        else:
            await update.message.reply_text(f"Дискриминант D = {D}. Уравнение не имеет действительных корней.")
    except ValueError:
        await update.message.reply_text("Ошибка: убедитесь, что вы ввели числовые значения для a, b и c.")

BJ_STATE_BETTING = "BETTING"
BJ_STATE_PLAYER_TURN = "PLAYER_TURN"

# Значение каждой карты
CARD_VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
    'J': 10, 'Q': 10, 'K': 10, 'A': 11
}

SUITS = ['♠', '♥', '♦', '♣']
RANKS = list(CARD_VALUES.keys())

# Функция для подсчета очков руки
def calculate_hand(hand):
    value = sum(CARD_VALUES[card[0]] for card in hand)
    aces = sum(1 for card in hand if card[0] == 'A')
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value

BJ_MIN_BET = 10
BJ_BETS = (10, 25, 50, 100)
BJ_START_BALANCE = 1000.0

# Кнопки для управления ходом игры
BUTTONS = {
    'hit': InlineKeyboardButton(text="Взять карту", callback_data='BJ_HIT'),
    'stand': InlineKeyboardButton(text="Остаться", callback_data='BJ_STAND'),
    'double': InlineKeyboardButton(text="Удвоить", callback_data='BJ_DOUBLE')
}


def format_hand(hand):
    return " ".join([f"{rank}{suit}" for rank, suit in hand])


def get_balance_filename(user_id: int) -> str:
    return f"{user_id}_balance.txt"


def load_balance(user_id: int) -> float:
    filename = get_balance_filename(user_id)
    if not os.path.exists(filename):
        return BJ_START_BALANCE

    try:
        with open(filename, "r", encoding="utf-8") as file:
            return max(float(file.read().strip()), 0.0)
    except (OSError, ValueError):
        return BJ_START_BALANCE


def save_balance(user_id: int, balance: float):
    filename = get_balance_filename(user_id)
    with open(filename, "w", encoding="utf-8") as file:
        file.write(f"{balance:.2f}")


def create_deck():
    deck = [(rank, suit) for rank in RANKS for suit in SUITS]
    random.shuffle(deck)
    return deck


def build_bet_keyboard(balance: float) -> InlineKeyboardMarkup:
    rows = []
    for amount in BJ_BETS:
        if amount <= balance:
            rows.append([InlineKeyboardButton(text=f"${amount}", callback_data=f"BJ_BET_{amount}")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="BJ_CANCEL")])
    return InlineKeyboardMarkup(rows)


def build_turn_keyboard(can_double: bool) -> InlineKeyboardMarkup:
    rows = [[BUTTONS['hit'], BUTTONS['stand']]]
    if can_double:
        rows.append([BUTTONS['double']])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="BJ_CANCEL")])
    return InlineKeyboardMarkup(rows)


def clear_blackjack_data(context: ContextTypes.DEFAULT_TYPE):
    for key in ("bj_bet", "bj_deck", "bj_player_hand", "bj_dealer_hand", "bj_state"):
        context.user_data.pop(key, None)


def get_round_view(context: ContextTypes.DEFAULT_TYPE, reveal_dealer: bool = False) -> str:
    player_hand = context.user_data["bj_player_hand"]
    dealer_hand = context.user_data["bj_dealer_hand"]
    player_points = calculate_hand(player_hand)

    if reveal_dealer:
        dealer_cards_text = format_hand(dealer_hand)
        dealer_points_text = str(calculate_hand(dealer_hand))
    else:
        dealer_cards_text = f"{dealer_hand[0][0]}{dealer_hand[0][1]} ??"
        dealer_points_text = "?"

    return (
        f"Ставка: ${context.user_data['bj_bet']:.2f}\n"
        f"Ваши карты: {format_hand(player_hand)} (очки: {player_points})\n"
        f"Карты дилера: {dealer_cards_text} (очки: {dealer_points_text})"
    )


def settle_round(balance: float, bet_amount: float, outcome: str) -> float:
    if outcome == "win":
        return balance + bet_amount
    if outcome == "lose":
        return max(balance - bet_amount, 0.0)
    return balance


def determine_outcome(player_value: int, dealer_value: int) -> str:
    if player_value > 21:
        return "lose"
    if dealer_value > 21:
        return "win"
    if player_value > dealer_value:
        return "win"
    if player_value < dealer_value:
        return "lose"
    return "push"


async def finish_blackjack_round(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    outcome: str,
    reason: str
):
    query = update.callback_query
    player_value = calculate_hand(context.user_data["bj_player_hand"])
    dealer_value = calculate_hand(context.user_data["bj_dealer_hand"])
    bet_amount = context.user_data["bj_bet"]
    old_balance = context.user_data["balance"]
    new_balance = settle_round(old_balance, bet_amount, outcome)
    delta = new_balance - old_balance

    context.user_data["balance"] = new_balance
    save_balance(update.effective_user.id, new_balance)

    if outcome == "win":
        result_text = "Вы выиграли!"
    elif outcome == "lose":
        result_text = "Вы проиграли."
    else:
        result_text = "Ничья."

    await query.edit_message_text(
        (
            f"{get_round_view(context, reveal_dealer=True)}\n\n"
            f"{reason}\n"
            f"{result_text}\n"
            f"Изменение баланса: {delta:+.2f}\n"
            f"Новый баланс: ${new_balance:.2f}\n\n"
            f"Новая игра: /play"
        )
    )

    clear_blackjack_data(context)


def dealer_play(context: ContextTypes.DEFAULT_TYPE):
    dealer_hand = context.user_data["bj_dealer_hand"]
    deck = context.user_data["bj_deck"]
    while calculate_hand(dealer_hand) < 17 and deck:
        dealer_hand.append(deck.pop())


# Начало игры
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = load_balance(user_id)
    context.user_data["balance"] = balance
    clear_blackjack_data(context)

    if balance < BJ_MIN_BET:
        await update.message.reply_text(
            (
                f"У вас ${balance:.2f}. Минимальная ставка: ${BJ_MIN_BET}.\n"
                f"Недостаточно средств для игры."
            )
        )
        return

    await update.message.reply_text(
        (
            f"Игра 21 началась.\n"
            f"Ваш баланс: ${balance:.2f}\n"
            f"Выберите ставку:"
        ),
        reply_markup=build_bet_keyboard(balance)
    )
    context.user_data["bj_state"] = BJ_STATE_BETTING


# Выбор ставки
async def select_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        bet_amount = float(query.data.replace("BJ_BET_", ""))
    except ValueError:
        await query.edit_message_text("Некорректная ставка.")
        clear_blackjack_data(context)
        return

    balance = context.user_data.get("balance", load_balance(update.effective_user.id))
    if bet_amount > balance or bet_amount < BJ_MIN_BET:
        await query.edit_message_text("Некорректная ставка. Начните заново: /play")
        clear_blackjack_data(context)
        return

    deck = create_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]

    context.user_data["bj_bet"] = bet_amount
    context.user_data["bj_deck"] = deck
    context.user_data["bj_player_hand"] = player_hand
    context.user_data["bj_dealer_hand"] = dealer_hand

    player_value = calculate_hand(player_hand)
    dealer_value = calculate_hand(dealer_hand)

    if player_value == 21 or dealer_value == 21:
        if player_value == 21 and dealer_value == 21:
            outcome = "push"
            reason = "У обоих 21 на раздаче."
        elif player_value == 21:
            outcome = "win"
            reason = "У вас 21 на раздаче."
        else:
            outcome = "lose"
            reason = "У дилера 21 на раздаче."
        await finish_blackjack_round(update, context, outcome, reason)
        return

    await query.edit_message_text(
        get_round_view(context, reveal_dealer=False),
        reply_markup=build_turn_keyboard(can_double=(balance >= bet_amount))
    )
    context.user_data["bj_state"] = BJ_STATE_PLAYER_TURN


# Ход игрока
async def player_turn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "BJ_HIT":
        new_card = context.user_data["bj_deck"].pop()
        context.user_data["bj_player_hand"].append(new_card)
        player_value = calculate_hand(context.user_data["bj_player_hand"])

        if player_value > 21:
            await finish_blackjack_round(update, context, "lose", "Перебор у игрока.")
            return

        await query.edit_message_text(
            get_round_view(context, reveal_dealer=False),
            reply_markup=build_turn_keyboard(can_double=False)
        )
        context.user_data["bj_state"] = BJ_STATE_PLAYER_TURN
        return

    if action == "BJ_DOUBLE":
        player_hand = context.user_data["bj_player_hand"]
        current_bet = context.user_data["bj_bet"]
        balance = context.user_data["balance"]

        if len(player_hand) != 2 or balance < current_bet:
            await query.answer("Удвоение сейчас недоступно.", show_alert=True)
            await query.edit_message_reply_markup(reply_markup=build_turn_keyboard(can_double=False))
            context.user_data["bj_state"] = BJ_STATE_PLAYER_TURN
            return

        context.user_data["bj_bet"] = current_bet * 2
        player_hand.append(context.user_data["bj_deck"].pop())

        if calculate_hand(player_hand) > 21:
            await finish_blackjack_round(update, context, "lose", "Перебор после удвоения.")
            return

        dealer_play(context)
        player_value = calculate_hand(player_hand)
        dealer_value = calculate_hand(context.user_data["bj_dealer_hand"])
        outcome = determine_outcome(player_value, dealer_value)
        await finish_blackjack_round(update, context, outcome, "Удвоение выполнено.")
        return

    if action == "BJ_STAND":
        dealer_play(context)
        player_value = calculate_hand(context.user_data["bj_player_hand"])
        dealer_value = calculate_hand(context.user_data["bj_dealer_hand"])
        outcome = determine_outcome(player_value, dealer_value)
        await finish_blackjack_round(update, context, outcome, "Игрок остановился.")
        return

    await query.answer("Неизвестное действие.", show_alert=True)
    context.user_data["bj_state"] = BJ_STATE_PLAYER_TURN


async def blackjack_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    state = context.user_data.get("bj_state")

    if data == "BJ_CANCEL":
        await cancel(update, context)
        return

    if data.startswith("BJ_BET_"):
        if state != BJ_STATE_BETTING:
            await query.answer("Сначала запусти новую игру: /play", show_alert=True)
            return
        await select_bet(update, context)
        return

    if data in {"BJ_HIT", "BJ_STAND", "BJ_DOUBLE"}:
        if state != BJ_STATE_PLAYER_TURN:
            await query.answer("Ставка еще не выбрана. Начни с /play", show_alert=True)
            return
        await player_turn(update, context)
        return

    await query.answer()


# Отмена игры
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_blackjack_data(context)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Игра 21 отменена.")
    elif update.message:
        await update.message.reply_text("Игра 21 отменена.")

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    application.add_handler(CommandHandler("yt", yt_command))
    application.add_handler(CommandHandler("start_makaka", start_maka))
    application.add_handler(CallbackQueryHandler(walk_callback, pattern='^walk$'))
    application.add_handler(CallbackQueryHandler(feed_callback, pattern='^(feed|breakfast)$'))
    application.add_handler(CommandHandler("score", score))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("guess", guess_number))
    application.add_handler(CommandHandler("comand", comand))
    application.add_handler(CommandHandler("settimer", settimer))
    application.add_handler(CommandHandler("settimerJOB", settimer_job_queue))
    application.add_handler(CommandHandler("start_spam", start_spam))
    application.add_handler(CommandHandler("getWeather", get_weather))
    application.add_handler(CommandHandler("setTimerWeather", start_timer_weather))
    application.add_handler(CommandHandler("startmagnitogorsk", start_magnitogorsk))
    application.add_handler(CommandHandler("stopmagnitogorsk", stop_magnitogorsk))
    application.add_handler(CommandHandler("getAstro", get_astronomy))
    application.add_handler(CommandHandler("startGetAstro", start_timer_astronomy))
    application.add_handler(CommandHandler("play_rpc", play_rpc))
    application.add_handler(CommandHandler("generate_image", generate_image))
    application.add_handler(CommandHandler("generate_image_ai", generate_image_ai))
    application.add_handler(MessageHandler(filters.PHOTO, edit_image_ai))
    application.add_handler(CommandHandler("poll", poll))
    application.add_handler(CommandHandler("discriminant", discriminant))
    application.add_handler(CommandHandler("play", start_game))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(blackjack_callback_router, pattern=r"^BJ_"))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("Бот запущен!")
    application.run_polling()


if __name__ == '__main__':
    main()
