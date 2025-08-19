# Chess Tactics Telegram Bot

Telegram-бот для тренировки шахматной тактики на основе ваших партий с Chess.com.

## 🚀 Быстрый старт

1. **Получите токен бота:**
   - Напишите [@BotFather](https://t.me/BotFather) в Telegram
   - Создайте бота: `/newbot`
   - Скопируйте токен

2. **Настройте и запустите:**
   ```bash
   # Скопируйте конфигурацию
   cp env.example .env
   
   # Отредактируйте .env - добавьте токен
   nano .env
   
   # Запустите Docker
   docker-compose up -d
   ```

3. **Используйте бота:**
   - Найдите своего бота в Telegram
   - `/update your_chess_username` - загрузить партии
   - `/start` - начать решать задачи

## 📱 Команды бота

- `/start` - Выбрать сложность и решать задачи
- `/update username [months] [depth]` - Загрузить партии с Chess.com
- `/stats` - Статистика по дриллам
- `/help` - Справка

**Примеры:**
- `/update hikaru` - анализ за 2 месяца
- `/update username 3 12` - 3 месяца, глубина 12

## 🔧 Как это работает

1. **Анализ партий**: Бот загружает ваши партии 5+0 с Chess.com, анализирует через Stockfish и находит бландеры (≥150cp потери)

2. **Создание дриллов**: Позиции с ошибками сохраняются как тактические задачи с разной сложностью (severity 1-3)

3. **Тренировка**: Бот показывает позицию как PNG, вы отправляете лучший ход, бот проверяет через Stockfish

## 📂 Структура

```
├── chess_trainer_bot.py     # Основной код (анализ + бот)
├── docker-compose.yml       # Docker конфигурация
├── Dockerfile              # Образ контейнера
├── requirements_docker.txt # Python зависимости
├── env.example             # Пример настроек
└── README_docker.md        # Детальная документация
```

## 💾 Данные

- База SQLite сохраняется в Docker-томе `chess_data`
- При перезапуске контейнера данные сохраняются
- Бэкап: `docker run --rm -v chess_data:/data -v $(pwd):/backup alpine cp /data/trainer_output.sqlite /backup/`

## 🛠 Локальный запуск (без Docker)

```bash
# Установите зависимости
pip install -r requirements_docker.txt

# Установите Stockfish
brew install stockfish  # macOS
sudo apt install stockfish  # Linux

# Настройте переменные
export TELEGRAM_BOT_TOKEN="your_token"
export STOCKFISH_PATH="/usr/games/stockfish"

# Запустите
python chess_trainer_bot.py
```

## 📊 Статистика

Бот создает дриллы по критериям:
- **Бландеры**: cp_loss ≥ 150
- **Долгие размышления**: time_spent > 20 сек
- **Severity**:
  - 1: легкие бландеры
  - 2: бландер + долгое размышление  
  - 3: грубые бландеры (≥300cp)

---

Подробная документация: [README_docker.md](README_docker.md)
