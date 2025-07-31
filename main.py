import os
import yt_dlp
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes
)

# --- 1. Функция для команды /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение при команде /start."""
    await update.message.reply_text('Привет! 👋\n\nОтправь мне ссылку на видео с YouTube, и я покажу меню для скачивания.')

# --- 2. Функция для отображения меню загрузки ---
async def show_download_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализирует ссылку, создаёт меню с короткими и безопасными callback_data."""
    url = update.message.text
    if not url or not ("youtube.com/" in url or "youtu.be/" in url):
        await update.message.reply_text("Пожалуйста, отправьте действительную ссылку на YouTube.")
        return

    msg = await update.message.reply_text('🔍 Ищу все доступные форматы...')

    ydl_opts = {
        'quiet': True,
        'cookiefile': 'cookies.txt',  # Используем cookies для обхода ограничений
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        video_id = info.get('id')
        thumbnail_url = info.get('thumbnail')
        title = info.get('title', 'Без названия')
        author = info.get('uploader', 'Неизвестен')
        duration_sec = info.get('duration', 0)
        upload_date_str = info.get('upload_date', '')

        # Форматируем дату из ГГГГММДД в ДД.ММ.ГГГГ
        formatted_date = f"{upload_date_str[6:8]}.{upload_date_str[4:6]}.{upload_date_str[0:4]}" if len(upload_date_str) == 8 else "N/A"
        # Форматируем продолжительность
        formatted_duration = str(timedelta(seconds=int(duration_sec)))

        caption = (
            f"**{title}**\n\n"
            f"👤 **Автор:** {author}\n"
            f"📅 **Дата:** {formatted_date}\n"
            f"⏳ **Продолжительность:** {formatted_duration}"
        )

        keyboard = []
        
        # Кнопка для скачивания аудио
        best_audio = next((f for f in reversed(info.get('formats', [])) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
        if best_audio:
            filesize_mb = (best_audio.get('filesize') or 0) / (1024*1024)
            button_text = f"🎧 Аудио / ~{filesize_mb:.1f} MB"
            # Кодируем данные для кнопки: действие, тип, код_формата, id_видео
            callback_data = f"dl:audio:a_{best_audio['format_id']}:{video_id}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

        # Кнопки для видео
        unique_formats = []
        seen_heights = set()
        for f in sorted(info.get('formats', []), key=lambda x: x.get('height', 0) or 0, reverse=True):
            if f.get('height') and f.get('ext') == 'mp4' and f.get('vcodec') != 'none' and f['height'] not in seen_heights:
                seen_heights.add(f['height'])
                unique_formats.append(f)
        
        for f in unique_formats:
            height = f['height']
            filesize_mb = (f.get('filesize') or f.get('filesize_approx', 0)) / (1024*1024)
            
            # Если у формата нет звука -> требует склейки. Кодируем как 'h_ВЫСОТА'.
            if f.get('acodec') == 'none':
                format_code = f"h_{height}"
            else: # Иначе это готовый файл. Кодируем как 'f_ID'.
                format_code = f"f_{f['format_id']}"
            
            # Пропускаем файлы больше 50 МБ (лимит Telegram)
            # if filesize_mb > 49.5: continue

            button_text = f"🎬 {height}p / ~{filesize_mb:.1f} MB"
            callback_data = f"dl:video:{format_code}:{video_id}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)

        if thumbnail_url:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=thumbnail_url, caption=caption, reply_markup=reply_markup, parse_mode='Markdown')
            await msg.delete()
        else:
            await msg.edit_text(caption, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        await msg.edit_text(f'🚫 Произошла ошибка при получении данных: {e}')

# --- 3. Функция-обработчик нажатий на кнопки ---
async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на inline-кнопки и 'расшифровывает' callback_data."""
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split(':', 3)
    if len(data_parts) != 4:
        await query.edit_message_text(text="Ошибка: неверные данные кнопки.")
        return

    action, media_type, format_code, video_id = data_parts
    
    if action == 'dl':
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # --- Логика расшифровки кода формата ---
        final_format_string = ""
        if format_code.startswith('h_'): # Требует склейки
            height = format_code.split('_')[1]
            final_format_string = f'bestvideo[height={height}][ext=mp4]+bestaudio[ext=m4a]/best[height={height}][ext=mp4]'
        elif format_code.startswith('f_'): # Готовый файл
            final_format_string = format_code.split('_', 1)[1]
        elif format_code.startswith('a_'): # Аудио
            final_format_string = format_code.split('_', 1)[1]

        if not final_format_string:
            await query.edit_message_text(text="Ошибка: не удалось определить формат для скачивания.")
            return

        await query.edit_message_caption(caption=query.message.caption_markdown + "\n\n*📥 Скачиваю выбранный формат...*", parse_mode='Markdown')

        ydl_opts = {
            'format': final_format_string,
            'outtmpl': f'{video_id}.%(ext)s',
            'cookiefile': 'cookies.txt',
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)

            await query.edit_message_caption(caption=query.message.caption_markdown + "\n\n*📤 Загружаю файл в Telegram...*", parse_mode='Markdown')
            
            with open(file_path, 'rb') as file:
                if media_type == 'video':
                    await context.bot.send_video(chat_id=query.message.chat_id, video=file, supports_streaming=True)
                else: # audio
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=file)
            
            await query.delete_message()
            os.remove(file_path)

        except Exception as e:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"🚫 Произошла ошибка при скачивании: {e}")
            await query.edit_message_caption(caption=query.message.caption_markdown) # Убираем статус при ошибке

# --- 4. Основной блок запуска бота ---
def main():
    """Запускает бота."""
    # Замените 'ВАШ_ТОКЕН' на токен вашего бота
    TOKEN = '8259544457:AAHB6ZdeYcpiKCvdljrASC_H6BEBKd7oWJ8'
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, show_download_menu))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    
    print("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
