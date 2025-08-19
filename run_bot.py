#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запуск Chess Tactics Telegram Bot с загрузкой конфигурации
"""

import os
import sys
from pathlib import Path

def load_env_file(filename="bot_config.env"):
    """Загрузка переменных окружения из файла"""
    env_path = Path(__file__).parent / filename
    
    if not env_path.exists():
        print(f"[WARN] Config file {env_path} not found")
        print(f"[INFO] Copy {env_path}.example to {env_path} and configure")
        return False
    
    try:
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
        print(f"[INFO] Loaded config from {env_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        return False

def check_requirements():
    """Проверка установленных зависимостей"""
    required_modules = [
        'telegram', 'chess', 'PIL', 'cairosvg'
    ]
    
    missing = []
    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    
    if missing:
        print(f"[ERROR] Missing required modules: {', '.join(missing)}")
        print(f"[INFO] Install with: pip install -r requirements_bot.txt")
        return False
    
    return True

def check_stockfish():
    """Проверка доступности Stockfish"""
    stockfish_path = os.getenv('STOCKFISH_PATH', '/opt/homebrew/bin/stockfish')
    
    if not os.path.exists(stockfish_path):
        print(f"[WARN] Stockfish not found at {stockfish_path}")
        print(f"[INFO] Install Stockfish:")
        print(f"  macOS: brew install stockfish")
        print(f"  Ubuntu: sudo apt install stockfish")
        print(f"[INFO] Or set STOCKFISH_PATH to correct location")
        return False
    
    print(f"[INFO] Stockfish found at {stockfish_path}")
    return True

def check_database():
    """Проверка базы данных"""
    db_path = os.getenv('CHESS_DB_PATH', './trainer_output.sqlite')
    
    if not os.path.exists(db_path):
        print(f"[ERROR] Database not found: {db_path}")
        print(f"[INFO] Run chess_trainer_sqlite.py first to generate drills")
        return False
    
    print(f"[INFO] Database found: {db_path}")
    return True

def main():
    """Основная функция запуска"""
    print("🏆 Chess Tactics Telegram Bot Launcher")
    print("=" * 40)
    
    # Загружаем конфигурацию
    if not load_env_file():
        sys.exit(1)
    
    # Проверяем зависимости
    if not check_requirements():
        sys.exit(1)
    
    # Проверяем Stockfish
    check_stockfish()  # не критично, бот может работать без движка
    
    # Проверяем базу данных
    if not check_database():
        sys.exit(1)
    
    # Проверяем токен бота
    if not os.getenv('TELEGRAM_BOT_TOKEN'):
        print("[ERROR] TELEGRAM_BOT_TOKEN not set")
        print("[INFO] Get bot token from @BotFather and add to bot_config.env")
        sys.exit(1)
    
    print("[INFO] All checks passed, starting bot...")
    print("=" * 40)
    
    # Импортируем и запускаем бота
    try:
        from telegram_chess_bot import main as bot_main
        bot_main()
    except KeyboardInterrupt:
        print("\n[INFO] Bot stopped by user")
    except Exception as e:
        print(f"[ERROR] Bot failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
