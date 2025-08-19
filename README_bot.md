# Chess Tactics Telegram Bot

Telegram-бот для тренировки шахматной тактики на основе дриллов из `trainer_output.sqlite`.

## Возможности

- 🎯 Генерация тактических задач из ваших партий
- 🖼️ Отрисовка позиций в PNG формате
- 🤖 Проверка ответов через Stockfish
- 📊 Статистика по дриллам
- 🎚️ Уровни сложности (легкий/средний/сложный)

## Установка и настройка

### 1. Установка зависимостей

```bash
pip install -r requirements_bot.txt
```

### 2. Установка Stockfish

**macOS:**
```bash
brew install stockfish
```

**Ubuntu/Debian:**
```bash
sudo apt install stockfish
```

### 3. Создание Telegram-бота

1. Найдите [@BotFather](https://t.me/BotFather) в Telegram
2. Создайте нового бота: `/newbot`
3. Скопируйте полученный токен

### 4. Конфигурация

Скопируйте файл конфигурации:
```bash
cp bot_config.env.example bot_config.env
```

Отредактируйте `bot_config.env`:
```bash
TELEGRAM_BOT_TOKEN=ваш_токен_от_BotFather
STOCKFISH_PATH=/opt/homebrew/bin/stockfish
CHESS_DB_PATH=./trainer_output.sqlite
```

### 5. Генерация дриллов

Если еще не создали базу с дриллами:
```bash
python chess_trainer_sqlite.py --user ваш_username --months 2 --only-5plus0
```

## Запуск

```bash
python run_bot.py
```

Или напрямую:
```bash
python telegram_chess_bot.py
```

## Использование

1. **Старт:** `/start` - выбор сложности задач
2. **Решение:** Отправьте ход в формате SAN (например: `Nf3`, `Qxh7+`)
3. **Статистика:** `/stats` - статистика по дриллам
4. **Справка:** `/help` - список команд

### Форматы ходов

- Простые ходы: `e4`, `Nf3`, `Bc4`
- Взятия: `Nxe5`, `Qxh7`, `exd5`
- Шах: `Qh5+`, `Bb5+`
- Мат: `Qh7#`
- Рокировки: `O-O`, `O-O-O`

## Структура проекта

```
├── telegram_chess_bot.py      # Основной код бота
├── run_bot.py                 # Скрипт запуска с проверками
├── requirements_bot.txt       # Зависимости
├── bot_config.env.example     # Пример конфигурации
├── chess_trainer_sqlite.py    # Генератор дриллов
└── trainer_output.sqlite      # База данных с дриллами
```

## Алгоритм работы

1. **Получение задач:** SQL-запрос к таблице `drills` с фильтрацией по сложности
2. **Отрисовка позиций:** Конвертация FEN → SVG → PNG через `python-chess`
3. **Проверка ответов:** 
   - Прямое сравнение с `engine_best_san`
   - Анализ через Stockfish для оценки качества хода
   - Допустимая потеря ≤ 50 cp считается хорошим ходом

## Устранение проблем

### Ошибка "Stockfish not found"
Убедитесь, что Stockfish установлен и путь в `STOCKFISH_PATH` корректен.

### Ошибка "Database not found"
Сначала сгенерируйте дриллы через `chess_trainer_sqlite.py`.

### Ошибка рендеринга доски
Если `cairosvg` не работает, бот использует упрощенную отрисовку через PIL.

### Проблемы с форматом ходов
Используйте стандартную шахматную нотацию (SAN). Примеры в `/help`.

## Дополнительно

- Бот сохраняет текущую задачу в памяти для каждого пользователя
- Статистика берется из последнего прогона (`MAX(run_id)`)
- Поддерживается работа без Stockfish (только прямое сравнение ходов)
