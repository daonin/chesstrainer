#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chess Tactics Telegram Bot
===========================
Читает дриллы из trainer_output.sqlite, генерирует PNG позиций
и проверяет ответы через Stockfish.
"""

import os
import io
import sqlite3
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime

import chess
import chess.engine
import chess.svg
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Конфигурация
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
STOCKFISH_PATH = os.getenv('STOCKFISH_PATH', '/opt/homebrew/bin/stockfish')
DB_PATH = os.getenv('CHESS_DB_PATH', './trainer_output.sqlite')

BOARD_SIZE = 400
ACCEPTABLE_CP_LOSS = 50  # ход считается хорошим, если проигрыш <= 50 cp

class ChessBot:
    def __init__(self):
        self.engine = None
        self.user_sessions = {}  # user_id -> current drill info
        
    async def init_engine(self):
        """Инициализация Stockfish"""
        if not os.path.exists(STOCKFISH_PATH):
            print(f"[ERROR] Stockfish not found at {STOCKFISH_PATH}")
            return False
        
        try:
            self.engine = await chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
            await self.engine.configure({"Threads": 1, "Hash": 64})
            print(f"[INFO] Stockfish initialized: {self.engine.id}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to init Stockfish: {e}")
            return False
    
    async def cleanup(self):
        """Закрытие движка"""
        if self.engine:
            await self.engine.quit()

    def get_db_connection(self) -> sqlite3.Connection:
        """Подключение к базе данных"""
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Database not found: {DB_PATH}")
        return sqlite3.connect(DB_PATH)

    def get_drill_query(self, difficulty: str = "medium", limit: int = 20) -> str:
        """SQL запрос для получения дриллов"""
        severity_filter = {
            "easy": "severity >= 1",
            "medium": "severity >= 2", 
            "hard": "severity >= 3"
        }.get(difficulty, "severity >= 2")
        
        return f"""
        SELECT drill_id, fen_before, engine_best_san, pv_best, san_played, 
               tags, difficulty, cp_loss, severity, phase
        FROM drills
        WHERE run_id = (SELECT MAX(id) FROM run_meta)
          AND {severity_filter}
          AND engine_best_san IS NOT NULL
        ORDER BY severity DESC, cp_loss DESC
        LIMIT {limit}
        """

    def fetch_drills(self, difficulty: str = "medium") -> List[Dict]:
        """Получение дриллов из базы"""
        with self.get_db_connection() as conn:
            cursor = conn.execute(self.get_drill_query(difficulty))
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def render_board_png(self, fen: str) -> bytes:
        """Генерация PNG изображения доски из FEN"""
        try:
            board = chess.Board(fen)
            
            # Генерируем SVG
            svg_data = chess.svg.board(
                board, 
                size=BOARD_SIZE,
                coordinates=True,
                style="""
                .square.light { fill: #f0d9b5; }
                .square.dark { fill: #b58863; }
                .coord { font-size: 14px; font-family: Arial; }
                """
            )
            
            # Конвертируем SVG в PNG через cairosvg
            try:
                import cairosvg
                png_data = cairosvg.svg2png(bytestring=svg_data.encode('utf-8'))
                return png_data
            except ImportError:
                # Fallback: простая PNG генерация без cairosvg
                return self._render_simple_board(board)
                
        except Exception as e:
            print(f"[ERROR] Board rendering failed: {e}")
            return self._create_error_image()

    def _render_simple_board(self, board: chess.Board) -> bytes:
        """Простая отрисовка доски без SVG"""
        img = Image.new('RGB', (BOARD_SIZE, BOARD_SIZE), 'white')
        draw = ImageDraw.Draw(img)
        
        square_size = BOARD_SIZE // 8
        
        # Рисуем клетки
        for rank in range(8):
            for file in range(8):
                x1 = file * square_size
                y1 = (7-rank) * square_size
                x2 = x1 + square_size
                y2 = y1 + square_size
                
                color = '#f0d9b5' if (rank + file) % 2 == 0 else '#b58863'
                draw.rectangle([x1, y1, x2, y2], fill=color)
                
                # Добавляем символы фигур (упрощенно)
                square = chess.square(file, rank)
                piece = board.piece_at(square)
                if piece:
                    piece_char = self._piece_to_unicode(piece)
                    # Примерная позиция для текста
                    text_x = x1 + square_size // 2
                    text_y = y1 + square_size // 2
                    draw.text((text_x, text_y), piece_char, fill='black', anchor='mm')
        
        # Сохраняем в байты
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return buffer.getvalue()

    def _piece_to_unicode(self, piece: chess.Piece) -> str:
        """Конвертация фигуры в Unicode символ"""
        symbols = {
            (chess.KING, chess.WHITE): '♔', (chess.KING, chess.BLACK): '♚',
            (chess.QUEEN, chess.WHITE): '♕', (chess.QUEEN, chess.BLACK): '♛',
            (chess.ROOK, chess.WHITE): '♖', (chess.ROOK, chess.BLACK): '♜',
            (chess.BISHOP, chess.WHITE): '♗', (chess.BISHOP, chess.BLACK): '♝',
            (chess.KNIGHT, chess.WHITE): '♘', (chess.KNIGHT, chess.BLACK): '♞',
            (chess.PAWN, chess.WHITE): '♙', (chess.PAWN, chess.BLACK): '♟',
        }
        return symbols.get((piece.piece_type, piece.color), '?')

    def _create_error_image(self) -> bytes:
        """Создание изображения с ошибкой"""
        img = Image.new('RGB', (BOARD_SIZE, BOARD_SIZE), 'lightgray')
        draw = ImageDraw.Draw(img)
        draw.text((BOARD_SIZE//2, BOARD_SIZE//2), 'Error\nrendering\nboard', 
                 fill='red', anchor='mm')
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return buffer.getvalue()

    async def analyze_move(self, fen: str, move_san: str, best_san: str) -> Dict:
        """Анализ хода через Stockfish"""
        if not self.engine:
            return {"valid": False, "error": "Engine not available"}
            
        try:
            board = chess.Board(fen)
            
            # Прямое сравнение с лучшим ходом
            if move_san.strip() == best_san.strip():
                return {
                    "valid": True,
                    "quality": "best",
                    "message": f"Отлично! Это лучший ход: {move_san}",
                    "cp_loss": 0
                }
            
            # Проверяем валидность хода
            try:
                move = board.parse_san(move_san)
            except ValueError:
                return {
                    "valid": False,
                    "error": f"Некорректный ход: {move_san}"
                }
            
            # Анализируем позицию до хода
            info_before = await self.engine.analyse(board, chess.engine.Limit(depth=15))
            eval_before = info_before.get("score")
            
            # Делаем ход и анализируем после
            board.push(move)
            info_after = await self.engine.analyse(board, chess.engine.Limit(depth=15))
            eval_after = info_after.get("score")
            
            if eval_before and eval_after:
                # Рассчитываем потерю в сантипешках
                cp_before = eval_before.pov(chess.WHITE).score() or 0
                cp_after = eval_after.pov(chess.WHITE).score() or 0
                
                # Потеря с точки зрения игрока, который ходил
                cp_loss = abs(cp_before - cp_after)
                
                if cp_loss <= ACCEPTABLE_CP_LOSS:
                    quality = "good"
                    message = f"Хороший ход! {move_san} (потеря: {cp_loss} cp)"
                elif cp_loss <= 100:
                    quality = "acceptable" 
                    message = f"Неплохо: {move_san} (потеря: {cp_loss} cp)\nЛучше было: {best_san}"
                else:
                    quality = "poor"
                    message = f"Не лучший выбор: {move_san} (потеря: {cp_loss} cp)\nЛучший ход: {best_san}"
                
                return {
                    "valid": True,
                    "quality": quality,
                    "message": message,
                    "cp_loss": cp_loss
                }
            
            return {
                "valid": True,
                "quality": "unknown",
                "message": f"Ход сделан: {move_san}\nНе удалось оценить качество"
            }
            
        except Exception as e:
            print(f"[ERROR] Move analysis failed: {e}")
            return {
                "valid": False,
                "error": f"Ошибка анализа: {str(e)}"
            }


# Инициализация бота
bot = ChessBot()

# Хэндлеры Telegram
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    keyboard = [
        [InlineKeyboardButton("Легкие задачи", callback_data="drill_easy")],
        [InlineKeyboardButton("Средние задачи", callback_data="drill_medium")],
        [InlineKeyboardButton("Сложные задачи", callback_data="drill_hard")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🏆 Добро пожаловать в Chess Tactics Bot!\n\n"
        "Я буду показывать вам позиции из ваших партий, где были сделаны неточности.\n"
        "Ваша задача — найти лучший ход!\n\n"
        "Выберите сложность:",
        reply_markup=reply_markup
    )

async def drill_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора сложности"""
    query = update.callback_query
    await query.answer()
    
    difficulty = query.data.split('_')[1]  # drill_easy -> easy
    
    try:
        drills = bot.fetch_drills(difficulty)
        if not drills:
            await query.message.reply_text(f"❌ Не найдено задач уровня '{difficulty}'")
            return
        
        # Выбираем случайный дрилл
        import random
        drill = random.choice(drills)
        
        # Сохраняем в сессию пользователя
        user_id = query.from_user.id
        bot.user_sessions[user_id] = drill
        
        # Генерируем изображение доски
        png_data = bot.render_board_png(drill['fen_before'])
        
        # Формируем описание
        tags = drill.get('tags', '').split(',')
        tag_text = ', '.join([f"#{tag}" for tag in tags if tag.strip()])
        
        caption = (
            f"🎯 **Найдите лучший ход!**\n"
            f"⚡ Сложность: {difficulty}\n"
            f"📊 Фаза: {drill.get('phase', 'unknown')}\n"
            f"🏷 {tag_text}\n\n"
            f"Сыгранный ход: `{drill['san_played']}`\n"
            f"Потеря: {drill.get('cp_loss', '?')} cp\n\n"
            f"💡 Отправьте лучший ход в ответ на это сообщение!"
        )
        
        await query.message.reply_photo(
            photo=io.BytesIO(png_data),
            caption=caption,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        print(f"[ERROR] Drill callback failed: {e}")
        await query.message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ответа пользователя"""
    user_id = update.from_user.id
    
    if user_id not in bot.user_sessions:
        await update.message.reply_text(
            "❓ Сначала выберите задачу с помощью /start"
        )
        return
    
    drill = bot.user_sessions[user_id]
    user_move = update.message.text.strip()
    
    # Проверяем ответ
    analysis = await bot.analyze_move(
        drill['fen_before'], 
        user_move, 
        drill['engine_best_san']
    )
    
    if not analysis["valid"]:
        error_msg = analysis.get("error", "Неизвестная ошибка")
        await update.message.reply_text(f"❌ {error_msg}")
        return
    
    # Формируем ответ
    quality = analysis["quality"]
    message = analysis["message"]
    
    emoji_map = {
        "best": "🏆",
        "good": "✅", 
        "acceptable": "⚠️",
        "poor": "❌",
        "unknown": "🤔"
    }
    
    emoji = emoji_map.get(quality, "🤔")
    
    response = f"{emoji} {message}"
    
    # Добавляем PV если есть
    if drill.get('pv_best'):
        try:
            pv_moves = json.loads(drill['pv_best'])
            if len(pv_moves) > 1:
                response += f"\n\n📋 Вариант: {' '.join(pv_moves[:5])}"
        except:
            pass
    
    # Кнопка для новой задачи
    keyboard = [[InlineKeyboardButton("Следующая задача", callback_data="drill_medium")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(response, reply_markup=reply_markup)
    
    # Очищаем сессию
    del bot.user_sessions[user_id]

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats - статистика по дриллам"""
    try:
        with bot.get_db_connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_drills,
                    AVG(cp_loss) as avg_cp_loss,
                    COUNT(CASE WHEN severity >= 3 THEN 1 END) as severe_blunders,
                    COUNT(CASE WHEN severity = 2 THEN 1 END) as medium_blunders,
                    COUNT(CASE WHEN severity = 1 THEN 1 END) as light_blunders
                FROM drills 
                WHERE run_id = (SELECT MAX(id) FROM run_meta)
            """)
            
            stats = cursor.fetchone()
            
            response = (
                f"📊 **Статистика дриллов**\n\n"
                f"🎯 Всего задач: {stats[0]}\n"
                f"📈 Средняя потеря: {stats[1]:.1f} cp\n\n"
                f"🔴 Грубые ошибки: {stats[2]}\n"
                f"🟡 Средние ошибки: {stats[3]}\n"
                f"🟢 Легкие ошибки: {stats[4]}"
            )
            
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка получения статистики: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
🏆 **Chess Tactics Bot - Справка**

**Команды:**
/start - Начать решение задач
/stats - Статистика дриллов
/help - Эта справка

**Как пользоваться:**
1. Выберите уровень сложности
2. Изучите позицию на доске
3. Найдите лучший ход
4. Отправьте ход в формате SAN (например: Nf3, Qxh7+)

**Форматы ходов:**
- Простые ходы: e4, Nf3, Bc4
- Взятия: Nxe5, Qxh7, exd5
- Шахи: Qh5+, Bb5+
- Маты: Qh7#
- Рокировки: O-O, O-O-O

Удачи в тренировках! 🚀
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    """Запуск бота"""
    if not BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN not set")
        return
    
    print(f"[INFO] Starting Chess Tactics Bot...")
    print(f"[INFO] Database: {DB_PATH}")
    print(f"[INFO] Stockfish: {STOCKFISH_PATH}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем хэндлеры
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(drill_callback, pattern="^drill_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
    
    # Инициализация и запуск
    async def startup():
        await bot.init_engine()
        
    async def shutdown():
        await bot.cleanup()
    
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
