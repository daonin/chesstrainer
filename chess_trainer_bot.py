#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chess Personal Trainer + Telegram Bot — Unified Edition
======================================================
Объединенный скрипт для анализа партий и телеграм-бота с тактическими задачами.

Режимы работы:
1. python chess_trainer_bot.py trainer --user username  # Анализ партий
2. python chess_trainer_bot.py bot                      # Запуск бота
3. python chess_trainer_bot.py both                     # Анализ + бот

Output: trainer_output.sqlite с таблицами:
  - run_meta, games, moves, drills
"""

import argparse, io, os, re, sys, time, json, sqlite3, math, asyncio
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path

import requests
import chess
import chess.pgn
import chess.engine
import chess.svg

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

# Telegram imports (опциональные)
try:
    from PIL import Image, ImageDraw, ImageFont
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("[WARN] Telegram dependencies not available. Only trainer mode will work.")

# Cairosvg for better board rendering (опционально)
try:
    import cairosvg
    CAIROSVG_AVAILABLE = True
except ImportError:
    CAIROSVG_AVAILABLE = False

# ---------- TRAINER CONSTANTS ----------
DEFAULT_USERNAME = "daonin"
DEFAULT_MONTHS_BACK = 2
DEFAULT_ONLY_5PLUS0 = True

ENGINE_DEPTH = 10
ENGINE_MOVETIME = 0.0
MAX_EVAL_POSITIONS = 2500

LONG_THINK_SEC = 20
FAST_MOVE_SEC = 5

BLUNDER_CP = 150
SEVERE_BLUNDER_CP = 300

OPENING_PLY = 14
MIDDLEGAME_PLY = 50

CLK_RE = re.compile(r"\[%clk\s+([0-9:]+)\]")

# ---------- BOT CONSTANTS ----------
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
STOCKFISH_PATH = os.getenv('STOCKFISH_PATH', '/opt/homebrew/bin/stockfish')
DB_PATH = os.getenv('CHESS_DB_PATH', '/data/trainer_output.sqlite')  # Docker volume path

BOARD_SIZE = 400
ACCEPTABLE_CP_LOSS = 50

# ---------- DATABASE SCHEMA ----------
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS run_meta (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  generated_at TEXT NOT NULL,
  user TEXT NOT NULL,
  engine_name TEXT,
  depth INTEGER,
  max_positions INTEGER,
  from_api INTEGER,
  months INTEGER,
  only_5plus0 INTEGER
);

CREATE TABLE IF NOT EXISTS games (
  game_id TEXT PRIMARY KEY,
  date_utc TEXT,
  white TEXT,
  black TEXT,
  time_control TEXT,
  result TEXT,
  termination TEXT
);

CREATE TABLE IF NOT EXISTS moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  game_id TEXT NOT NULL,
  ply INTEGER,
  side TEXT,
  phase TEXT,
  san TEXT,
  fen_before TEXT,
  clock_after_sec INTEGER,
  time_spent_sec INTEGER,
  eval_before_cp INTEGER,
  eval_after_cp INTEGER,
  cp_loss INTEGER,
  is_check INTEGER,
  is_capture INTEGER,
  is_pawn_push INTEGER,
  is_promotion INTEGER,
  is_castle INTEGER,
  FOREIGN KEY (run_id) REFERENCES run_meta(id),
  FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS drills (
  drill_id TEXT PRIMARY KEY,
  run_id INTEGER NOT NULL,
  game_id TEXT NOT NULL,
  ply INTEGER,
  side TEXT,
  phase TEXT,
  san_played TEXT,
  fen_before TEXT,
  time_spent_sec INTEGER,
  clock_after_sec INTEGER,
  cp_loss INTEGER,
  engine_best_san TEXT,
  eval_before_cp INTEGER,
  eval_after_cp INTEGER,
  pv_best TEXT,
  severity INTEGER,
  tags TEXT,
  difficulty TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES run_meta(id),
  FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_moves_run_game ON moves(run_id, game_id);
CREATE INDEX IF NOT EXISTS idx_drills_run_severity ON drills(run_id, severity DESC);
CREATE INDEX IF NOT EXISTS idx_drills_game_ply ON drills(game_id, ply);
"""

# ---------- SHARED UTILITIES ----------
def open_db(path: str) -> sqlite3.Connection:
    """Открытие базы данных с инициализацией схемы"""
    # Создаем директорию если не существует
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA_SQL)
    return conn

def init_engine(path: str) -> Optional[chess.engine.SimpleEngine]:
    """Инициализация движка"""
    if not path or not os.path.exists(path):
        print(f"[WARN] Stockfish not found at '{path}'")
        return None
    try:
        eng = chess.engine.SimpleEngine.popen_uci(path)
        try: 
            eng.configure({"Threads": 1, "Hash": 64})
        except Exception: 
            pass
        print(f"[INFO] Stockfish initialized: {eng.id}")
        return eng
    except Exception as e:
        print(f"[WARN] Cannot start engine '{path}': {e}")
        return None

async def init_engine_async(path: str) -> Optional[chess.engine.SimpleEngine]:
    """Асинхронная инициализация движка"""
    print(f"[DEBUG] Attempting to initialize engine at: {path}")
    print(f"[DEBUG] Path exists: {os.path.exists(path) if path else 'No path provided'}")
    
    if not path:
        print("[WARN] No Stockfish path provided")
        return None
        
    if not os.path.exists(path):
        print(f"[WARN] Stockfish not found at '{path}'")
        # Try common locations
        common_paths = ['/usr/bin/stockfish', '/usr/games/stockfish', '/usr/local/bin/stockfish']
        for alt_path in common_paths:
            if os.path.exists(alt_path):
                print(f"[INFO] Found Stockfish at alternative location: {alt_path}")
                path = alt_path
                break
        else:
            return None
    
    try:
        print(f"[DEBUG] Attempting to start engine at: {path}")
        transport, eng = await chess.engine.popen_uci(path)
        print(f"[DEBUG] Engine started, configuring...")
        await eng.configure({"Threads": 1, "Hash": 64})
        print(f"[INFO] Stockfish initialized async: {eng.id}")
        # Store transport in engine for cleanup
        eng._transport = transport
        return eng
    except Exception as e:
        print(f"[WARN] Cannot start engine async '{path}': {e}")
        import traceback
        traceback.print_exc()
        return None

# ---------- TRAINER FUNCTIONS ----------
def make_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept": "application/json, text/plain, */*"})
    return s

def http_get(session: requests.Session, url: str, max_retry: int, backoff: float, expect_json: bool):
    attempt = 0
    while True:
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 200:
                return r.json() if expect_json else r.text
            if r.status_code in (403, 429) or 500 <= r.status_code < 600:
                attempt += 1
                if attempt > max_retry: r.raise_for_status()
                sleep_for = backoff * (2 ** (attempt - 1))
                (tqdm.write if tqdm else print)(f"[WARN] {r.status_code} {url}; retry {attempt}/{max_retry} in {sleep_for:.1f}s")
                time.sleep(sleep_for); continue
            r.raise_for_status()
        except requests.RequestException as e:
            attempt += 1
            if attempt > max_retry: raise
            sleep_for = backoff * (2 ** (attempt - 1))
            (tqdm.write if tqdm else print)(f"[WARN] {e}; retry {attempt}/{max_retry} in {sleep_for:.1f}s")
            time.sleep(sleep_for)

def get_archives(session: requests.Session, username: str, max_retry: int, backoff: float) -> List[str]:
    url = f"https://api.chess.com/pub/player/{username}/games/archives"
    data = http_get(session, url, max_retry, backoff, expect_json=True)
    return data.get("archives", [])

def fetch_archive_pgn(session: requests.Session, archive_url: str, max_retry: int, backoff: float) -> str:
    pgn_url = archive_url + "/pgn"
    try:
        return http_get(session, pgn_url, max_retry, backoff, expect_json=False)
    except Exception:
        js = http_get(session, archive_url, max_retry, backoff, expect_json=True)
        pgns = [g.get("pgn", "") for g in js.get("games", []) if g.get("pgn")]
        return "\n\n".join(pgns)

def parse_clock(comment: str) -> Optional[int]:
    if not comment: return None
    m = CLK_RE.search(comment)
    if not m: return None
    t = m.group(1).strip()
    parts = [int(x) for x in t.split(":")]
    if len(parts)==2: mm,ss = parts; return mm*60+ss
    if len(parts)==3: hh,mm,ss = parts; return hh*3600+mm*60+ss
    return None

def parse_pgn_games(pgn_text: str) -> List[chess.pgn.Game]:
    games = []; pgn_io = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(pgn_io)
        if game is None: break
        games.append(game)
    return games

def is_5plus0_game(game: chess.pgn.Game) -> bool:
    tc = game.headers.get("TimeControl","")
    return tc in ("300", "300+0")

def phase_from_ply(ply: int) -> str:
    if ply <= OPENING_PLY: return "opening"
    if ply <= MIDDLEGAME_PLY: return "middlegame"
    return "endgame"

def san_motif_flags(san: str) -> Dict[str,int]:
    return {
        "is_check": 1 if "+" in san else 0,
        "is_mate": 1 if "#" in san else 0,
        "is_capture": 1 if "x" in san else 0,
        "is_pawn_push": 1 if san and san[0] in "abcdefgh" else 0,
        "is_promotion": 1 if "=" in san else 0,
        "is_castle": 1 if san in ("O-O","O-O-O") else 0,
    }

def analyze_position(eng: chess.engine.SimpleEngine, board: chess.Board, depth: int, movetime: float):
    if eng is None: return None
    limit = chess.engine.Limit(depth=depth) if movetime <= 0 else chess.engine.Limit(time=movetime)
    try: return eng.analyse(board, limit)
    except Exception as e:
        print(f"[WARN] Engine analyse failed: {e}"); return None

def cp_from_info(info) -> Optional[int]:
    if not info: return None
    sc = info.get("score"); 
    if not sc: return None
    try: return sc.pov(chess.WHITE).score()
    except Exception:
        try: return sc.white().score()
        except Exception: return None

def pv_to_san(board_before: chess.Board, pv_moves) -> Tuple[str, str]:
    """Return best_san and pv SAN list as JSON string."""
    if not pv_moves: return None, "[]"
    b = board_before.copy()
    san_list = []
    best_san = None
    for i, mv in enumerate(pv_moves):
        try:
            san = b.san(mv)
        except Exception:
            break
        san_list.append(san)
        if i==0: best_san = san
        b.push(mv)
    return best_san, json.dumps(san_list, ensure_ascii=False)

def analyze_and_store(conn: sqlite3.Connection, games: List[chess.pgn.Game], username: str,
                      only_5plus0: bool, eng, depth, movetime, max_positions, sample_every,
                      run_id: int):
    """Основная функция анализа и сохранения"""
    cur = conn.cursor()
    eval_used = 0

    it = games if not tqdm else tqdm(games, desc="Analyze", unit="game")
    for game in it:
        if only_5plus0 and not is_5plus0_game(game):
            continue

        white = game.headers.get("White","")
        black = game.headers.get("Black","")
        date = game.headers.get("UTCDate", game.headers.get("Date",""))
        result = game.headers.get("Result","")
        termination = game.headers.get("Termination","")
        time_control = game.headers.get("TimeControl","")
        gid = f"{date}_{white}_vs_{black}".strip("_")

        # upsert game
        cur.execute("""INSERT OR IGNORE INTO games(game_id, date_utc, white, black, time_control, result, termination)
                       VALUES(?,?,?,?,?,?,?)""", (gid, date, white, black, time_control, result, termination))

        board = game.board()
        node = game
        ply = 0
        last_clock = {"W": None, "B": None}

        while node.variations:
            next_node = node.variation(0)
            move = next_node.move
            san = next_node.san()
            ply += 1

            side = "W" if board.turn == chess.WHITE else "B"
            phase = phase_from_ply(ply)

            # sampling
            sample_skip = (sample_every > 1 and (ply % sample_every) != 0)

            # BEFORE eval
            eval_before = None
            info_before = None
            if not sample_skip and eng and eval_used < max_positions:
                info_before = analyze_position(eng, board, depth, movetime)
                eval_before = cp_from_info(info_before)

            # Записываем FEN ДО хода
            fen_before = board.fen()
            
            # timing
            comment = next_node.comment
            clk_after = parse_clock(comment)
            time_spent = None
            if clk_after is not None and last_clock[side] is not None:
                dt = last_clock[side] - clk_after
                if dt >= 0: time_spent = dt

            # push
            board.push(move)

            # AFTER eval
            eval_after = None
            if not sample_skip and eng and eval_used < max_positions:
                info_after = analyze_position(eng, board, depth, movetime)
                eval_after = cp_from_info(info_after)
                eval_used += 2
                if tqdm: it.set_postfix_str(f"engine_pos≈{eval_used}/{max_positions}")

            # cp loss for mover pov
            cp_loss = None
            if eval_before is not None and eval_after is not None:
                loss_white_pov = eval_before - eval_after
                cp_loss = loss_white_pov if side == "W" else -loss_white_pov

            flags = san_motif_flags(san)

            # update last clock for mover
            if clk_after is not None:
                last_clock[side] = clk_after

            # insert move
            cur.execute("""INSERT INTO moves(run_id, game_id, ply, side, phase, san, fen_before,
                                             clock_after_sec, time_spent_sec, eval_before_cp, eval_after_cp, cp_loss,
                                             is_check, is_capture, is_pawn_push, is_promotion, is_castle)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, gid, ply, side, phase, san, fen_before, clk_after, time_spent,
                         eval_before, eval_after, cp_loss,
                         flags["is_check"], flags["is_capture"], flags["is_pawn_push"],
                         flags["is_promotion"], flags["is_castle"]))

            # DRILL candidate?
            is_blunder = (cp_loss is not None and cp_loss >= BLUNDER_CP)
            is_long = (time_spent is not None and time_spent > LONG_THINK_SEC)

            if is_blunder or is_long:
                # derive best move from info_before if available
                engine_best_san = None
                pv_best = "[]"
                if info_before and "pv" in info_before and info_before["pv"]:
                    # Создаем новую доску из FEN до хода для анализа PV
                    board_for_pv = chess.Board(fen_before)
                    engine_best_san, pv_best = pv_to_san(board_for_pv, info_before["pv"])

                # severity ranking
                if cp_loss is not None and cp_loss >= SEVERE_BLUNDER_CP:
                    severity = 3
                elif is_blunder and is_long:
                    severity = 2
                elif is_blunder:
                    severity = 1
                else:
                    severity = 0

                tags = []
                if is_blunder: tags.append("blunder")
                if is_long:    tags.append("long-think")
                if flags["is_check"]: tags.append("check")
                if flags["is_capture"]: tags.append("capture")
                if flags["is_pawn_push"]: tags.append("pawn-push")

                # deterministic drill_id
                import hashlib
                src = f"{gid}|{ply}|{fen_before}|{engine_best_san or ''}"
                drill_id = hashlib.sha1(src.encode()).hexdigest()[:16]

                cur.execute("""INSERT OR REPLACE INTO drills(
                                  drill_id, run_id, game_id, ply, side, phase, san_played, fen_before,
                                  time_spent_sec, clock_after_sec, cp_loss, engine_best_san,
                                  eval_before_cp, eval_after_cp, pv_best, severity, tags, difficulty, created_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (drill_id, run_id, gid, ply, side, phase, san, fen_before, time_spent, clk_after,
                             cp_loss, engine_best_san, eval_before, eval_after, pv_best, severity,
                             ",".join(tags), "easy" if cp_loss and cp_loss<250 else "medium", datetime.utcnow().isoformat()+"Z"))

    conn.commit()

def summarize_stats(conn: sqlite3.Connection, run_id: int):
    """Вывод статистики"""
    cur = conn.cursor()
    
    cur.execute("""SELECT AVG(time_spent_sec) FROM moves WHERE run_id=? AND time_spent_sec IS NOT NULL""", (run_id,))
    avg_t = cur.fetchone()[0]

    cur.execute("""SELECT AVG(CASE WHEN time_spent_sec> ? THEN 1.0 ELSE 0.0 END)
                   FROM moves WHERE run_id=? AND time_spent_sec IS NOT NULL""", (LONG_THINK_SEC, run_id))
    p_long = cur.fetchone()[0]

    cur.execute("""SELECT AVG(CASE WHEN time_spent_sec< ? THEN 1.0 ELSE 0.0 END)
                   FROM moves WHERE run_id=? AND time_spent_sec IS NOT NULL""", (FAST_MOVE_SEC, run_id))
    p_fast = cur.fetchone()[0]

    cur.execute("""SELECT AVG(CASE WHEN cp_loss>= ? THEN 1.0 ELSE 0.0 END)
                   FROM moves WHERE run_id=? AND cp_loss IS NOT NULL""", (BLUNDER_CP, run_id))
    p_bl = cur.fetchone()[0]

    print("\n== Training Statistics ==")
    print(f"avg_time_per_move: {None if avg_t is None else round(avg_t,1)} sec")
    print(f"share_long(>{LONG_THINK_SEC}s): {None if p_long is None else round(p_long*100,1)}%")
    print(f"share_fast(<{FAST_MOVE_SEC}s): {None if p_fast is None else round(p_fast*100,1)}%")
    print(f"blunder_rate(≥{BLUNDER_CP}cp): {None if p_bl is None else round(p_bl*100,1)}%")

# ---------- BOT FUNCTIONS ----------
class ChessBot:
    def __init__(self):
        self.engine = None
        self.user_sessions = {}
        self.admin_users = set()
        
    async def init_engine(self):
        """Инициализация Stockfish для бота"""
        self.engine = await init_engine_async(STOCKFISH_PATH)
        return self.engine is not None
        
    async def cleanup(self):
        """Закрытие движка"""
        if self.engine:
            try:
                await self.engine.quit()
            except:
                pass
            # Close transport if stored
            if hasattr(self.engine, '_transport'):
                try:
                    self.engine._transport.close()
                except:
                    pass

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
        with open_db(DB_PATH) as conn:
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
            
            # Конвертируем SVG в PNG
            if CAIROSVG_AVAILABLE:
                png_data = cairosvg.svg2png(bytestring=svg_data.encode('utf-8'))
                return png_data
            else:
                return self._render_simple_board(board)
                
        except Exception as e:
            print(f"[ERROR] Board rendering failed: {e}")
            return self._create_error_image()

    def _render_simple_board(self, board: chess.Board) -> bytes:
        """Простая отрисовка доски без SVG"""
        if not TELEGRAM_AVAILABLE:
            return b''
            
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
                
                # Добавляем символы фигур
                square = chess.square(file, rank)
                piece = board.piece_at(square)
                if piece:
                    piece_char = self._piece_to_unicode(piece)
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
        if not TELEGRAM_AVAILABLE:
            return b''
            
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

    async def update_games_async(self, username: str, months: int = 2, max_positions: int = 2500, depth: int = 10, progress_callback=None, force_reanalyze: bool = False):
        """Асинхронное обновление базы данных с партиями"""
        if not self.engine:
            return {"success": False, "error": "Engine not available"}
        
        try:
            # Создаем сессию для API
            session = make_session(f"chess-personal-trainer (contact: chess-trainer@example.com)")
            
            # Получаем архивы
            if progress_callback:
                await progress_callback("📥 Получение списка партий...")
            
            archives = get_archives(session, username, 5, 1.0)
            archives = sorted(archives)[-months:]
            
            if not archives:
                return {"success": False, "error": f"Нет партий для пользователя {username}"}
            
            # Загружаем партии
            games = []
            for i, archive_url in enumerate(archives):
                if progress_callback:
                    await progress_callback(f"📥 Загрузка архива {i+1}/{len(archives)}...")
                
                pgn_text = fetch_archive_pgn(session, archive_url, 5, 1.0)
                archive_games = parse_pgn_games(pgn_text)
                
                # Фильтруем 5+0
                archive_games = [g for g in archive_games if is_5plus0_game(g)]
                games.extend(archive_games)
            
            if not games:
                return {"success": False, "error": "Нет партий 5+0 за указанный период"}
            
            # Анализируем партии
            if progress_callback:
                await progress_callback(f"🔍 Анализ {len(games)} партий...")
            
            conn = open_db(DB_PATH)
            engine_name = self.engine.id.get("name", "Stockfish")
            
            # Создаем новый run
            cur = conn.cursor()
            cur.execute("""INSERT INTO run_meta(generated_at,user,engine_name,depth,max_positions,from_api,months,only_5plus0)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (datetime.utcnow().isoformat()+"Z", username, engine_name, depth,
                         max_positions, 1, months, 1))
            run_id = cur.lastrowid
            conn.commit()
            
            # Анализируем с прогрессом
            await self._analyze_games_async(conn, games, username, run_id, depth, max_positions, progress_callback, force_reanalyze)
            
            # Статистика
            cur.execute("SELECT COUNT(*) FROM drills WHERE run_id = ?", (run_id,))
            drills_count = cur.fetchone()[0]
            
            # Получаем количество реально проанализированных партий из _analyze_games_async
            # Это будет в переменной analyzed_games, но мы можем посчитать из moves
            cur.execute("SELECT COUNT(DISTINCT game_id) FROM moves WHERE run_id = ?", (run_id,))
            new_games_analyzed = cur.fetchone()[0]
            
            conn.close()
            
            return {
                "success": True, 
                "games_total": len(games),
                "games_new": new_games_analyzed,
                "games_skipped": len(games) - new_games_analyzed,
                "drills": drills_count,
                "run_id": run_id
            }
            
        except Exception as e:
            return {"success": False, "error": f"Ошибка обновления: {str(e)}"}
    
    async def _analyze_games_async(self, conn, games, username, run_id, depth, max_positions, progress_callback, force_reanalyze: bool = False):
        """Асинхронный анализ партий с прогрессом"""
        cur = conn.cursor()
        eval_used = 0
        analyzed_games = 0
        skipped_games = 0
        
        for game_idx, game in enumerate(games):
            if progress_callback and game_idx % 10 == 0:
                mode_txt = "принудительно" if force_reanalyze else f"новых: {analyzed_games}, пропущено: {skipped_games}"
                await progress_callback(f"🔍 Партия {game_idx+1}/{len(games)} ({mode_txt})")
            
            white = game.headers.get("White","")
            black = game.headers.get("Black","")
            date = game.headers.get("UTCDate", game.headers.get("Date",""))
            result = game.headers.get("Result","")
            termination = game.headers.get("Termination","")
            time_control = game.headers.get("TimeControl","")
            gid = f"{date}_{white}_vs_{black}".strip("_")

            # Проверяем, была ли эта партия уже проанализирована (если не force)
            if not force_reanalyze:
                cur.execute("SELECT COUNT(*) FROM moves WHERE game_id = ?", (gid,))
                moves_count = cur.fetchone()[0]
                
                if moves_count > 0:
                    # Партия уже анализировалась, пропускаем
                    skipped_games += 1
                    cur.execute("""INSERT OR IGNORE INTO games(game_id, date_utc, white, black, time_control, result, termination)
                                   VALUES(?,?,?,?,?,?,?)""", (gid, date, white, black, time_control, result, termination))
                    continue
            
            analyzed_games += 1
            
            # upsert game
            cur.execute("""INSERT OR IGNORE INTO games(game_id, date_utc, white, black, time_control, result, termination)
                           VALUES(?,?,?,?,?,?,?)""", (gid, date, white, black, time_control, result, termination))

            board = game.board()
            node = game
            ply = 0
            last_clock = {"W": None, "B": None}

            while node.variations and eval_used < max_positions:
                next_node = node.variation(0)
                move = next_node.move
                san = next_node.san()
                ply += 1

                side = "W" if board.turn == chess.WHITE else "B"
                phase = phase_from_ply(ply)

                # BEFORE eval
                info_before = await self.engine.analyse(board, chess.engine.Limit(depth=depth))
                eval_before = cp_from_info(info_before)

                # Записываем FEN ДО хода
                fen_before = board.fen()
                
                # timing
                comment = next_node.comment
                clk_after = parse_clock(comment)
                time_spent = None
                if clk_after is not None and last_clock[side] is not None:
                    dt = last_clock[side] - clk_after
                    if dt >= 0: time_spent = dt

                # Делаем ход
                board.push(move)

                # AFTER eval
                info_after = await self.engine.analyse(board, chess.engine.Limit(depth=depth))
                eval_after = cp_from_info(info_after)
                eval_used += 2

                # cp loss for mover pov
                cp_loss = None
                if eval_before is not None and eval_after is not None:
                    loss_white_pov = eval_before - eval_after
                    cp_loss = loss_white_pov if side == "W" else -loss_white_pov

                flags = san_motif_flags(san)

                # update last clock
                if clk_after is not None:
                    last_clock[side] = clk_after

                # insert move
                cur.execute("""INSERT INTO moves(run_id, game_id, ply, side, phase, san, fen_before,
                                                 clock_after_sec, time_spent_sec, eval_before_cp, eval_after_cp, cp_loss,
                                                 is_check, is_capture, is_pawn_push, is_promotion, is_castle)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (run_id, gid, ply, side, phase, san, fen_before, clk_after, time_spent,
                             eval_before, eval_after, cp_loss,
                             flags["is_check"], flags["is_capture"], flags["is_pawn_push"],
                             flags["is_promotion"], flags["is_castle"]))

                # DRILL candidate?
                is_blunder = (cp_loss is not None and cp_loss >= BLUNDER_CP)
                is_long = (time_spent is not None and time_spent > LONG_THINK_SEC)

                if is_blunder or is_long:
                    engine_best_san = None
                    pv_best = "[]"
                    if info_before and "pv" in info_before and info_before["pv"]:
                        # Создаем доску из FEN до хода для анализа PV
                        board_for_pv = chess.Board(fen_before)
                        engine_best_san, pv_best = pv_to_san(board_for_pv, info_before["pv"])

                    # severity ranking
                    if cp_loss is not None and cp_loss >= SEVERE_BLUNDER_CP:
                        severity = 3
                    elif is_blunder and is_long:
                        severity = 2
                    elif is_blunder:
                        severity = 1
                    else:
                        severity = 0

                    tags = []
                    if is_blunder: tags.append("blunder")
                    if is_long: tags.append("long-think")
                    if flags["is_check"]: tags.append("check")
                    if flags["is_capture"]: tags.append("capture")
                    if flags["is_pawn_push"]: tags.append("pawn-push")

                    # deterministic drill_id
                    import hashlib
                    src = f"{gid}|{ply}|{fen_before}|{engine_best_san or ''}"
                    drill_id = hashlib.sha1(src.encode()).hexdigest()[:16]

                    cur.execute("""INSERT OR REPLACE INTO drills(
                                      drill_id, run_id, game_id, ply, side, phase, san_played, fen_before,
                                      time_spent_sec, clock_after_sec, cp_loss, engine_best_san,
                                      eval_before_cp, eval_after_cp, pv_best, severity, tags, difficulty, created_at)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (drill_id, run_id, gid, ply, side, phase, san, fen_before, time_spent, clk_after,
                                 cp_loss, engine_best_san, eval_before, eval_after, pv_best, severity,
                                 ",".join(tags), "easy" if cp_loss and cp_loss<250 else "medium", datetime.utcnow().isoformat()+"Z"))

        conn.commit()

# Глобальный экземпляр бота
bot = ChessBot()

# ---------- TELEGRAM HANDLERS ----------
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
    
    difficulty = query.data.split('_')[1]
    
    try:
        drills = bot.fetch_drills(difficulty)
        if not drills:
            await query.message.reply_text(f"❌ Не найдено задач уровня '{difficulty}'")
            return
        
        import random
        drill = random.choice(drills)
        
        user_id = query.from_user.id
        bot.user_sessions[user_id] = drill
        
        png_data = bot.render_board_png(drill['fen_before'])
        
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
    
    analysis = await bot.analyze_move(
        drill['fen_before'], 
        user_move, 
        drill['engine_best_san']
    )
    
    if not analysis["valid"]:
        error_msg = analysis.get("error", "Неизвестная ошибка")
        await update.message.reply_text(f"❌ {error_msg}")
        return
    
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
    
    if drill.get('pv_best'):
        try:
            pv_moves = json.loads(drill['pv_best'])
            if len(pv_moves) > 1:
                response += f"\n\n📋 Вариант: {' '.join(pv_moves[:5])}"
        except:
            pass
    
    keyboard = [[InlineKeyboardButton("Следующая задача", callback_data="drill_medium")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(response, reply_markup=reply_markup)
    del bot.user_sessions[user_id]

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats"""
    try:
        with open_db(DB_PATH) as conn:
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

async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /update - обновление базы данных"""
    user_id = update.message.from_user.id
    
    # Извлекаем параметры из команды
    args = context.args
    username = args[0] if args else None
    
    if not username:
        await update.message.reply_text(
            "❌ Укажите Chess.com username:\n"
            "`/update your_username`\n\n"
            "Опционально: `/update username months depth`\n"
            "Принудительно: `/update username months depth force`\n"
            "Например: `/update hikaru 3 12 force`",
            parse_mode='Markdown'
        )
        return
    
    months = int(args[1]) if len(args) > 1 else 2
    depth = int(args[2]) if len(args) > 2 else 10
    force_reanalyze = len(args) > 3 and args[3].lower() == 'force'
    max_positions = 2500
    
    # Проверяем лимиты
    if months > 6:
        await update.message.reply_text("❌ Максимум 6 месяцев")
        return
    if depth > 15:
        await update.message.reply_text("❌ Максимальная глубина 15")
        return
    
    # Отправляем начальное сообщение
    if force_reanalyze:
        initial_msg = "🚀 Начинаю ПРИНУДИТЕЛЬНОЕ обновление базы данных (пересканирую все партии)..."
    else:
        initial_msg = "🚀 Начинаю обновление базы данных (пропущу уже проанализированные партии)..."
    
    progress_msg = await update.message.reply_text(initial_msg)
    
    async def progress_callback(message):
        try:
            await progress_msg.edit_text(message)
        except:
            pass  # Игнорируем ошибки редактирования
    
    # Запускаем обновление
    result = await bot.update_games_async(username, months, max_positions, depth, progress_callback, force_reanalyze)
    
    if result["success"]:
        final_message = (
            f"✅ **Обновление завершено!**\n\n"
            f"👤 Пользователь: {username}\n"
            f"📥 Партий загружено: {result['games_total']}\n"
            f"🆕 Новых проанализировано: {result['games_new']}\n"
            f"⏭️ Пропущено (уже были): {result['games_skipped']}\n"
            f"🎯 Дриллов создано: {result['drills']}\n"
            f"📊 Run ID: {result['run_id']}\n\n"
            f"Теперь можете решать задачи через /start"
        )
    else:
        final_message = f"❌ **Ошибка обновления:**\n{result['error']}"
    
    await progress_msg.edit_text(final_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
🏆 **Chess Tactics Bot - Справка**

**Команды:**
/start - Начать решение задач
/update username - Обновить базу партий  
/stats - Статистика дриллов
/help - Эта справка

**Обновление базы:**
`/update your_username` - анализ за 2 месяца
`/update username 3 12` - 3 месяца, глубина 12
`/update username 2 10 force` - принудительный пересканирование

**Как решать задачи:**
1. /start → выберите сложность
2. Изучите позицию на доске
3. Найдите лучший ход
4. Отправьте ход в формате SAN

**Форматы ходов:**
- Простые: e4, Nf3, Bc4
- Взятия: Nxe5, Qxh7, exd5  
- Шахи: Qh5+, Bb5+
- Маты: Qh7#
- Рокировки: O-O, O-O-O

Удачи в тренировках! 🚀
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

# ---------- ARGUMENT PARSING ----------
def parse_args():
    ap = argparse.ArgumentParser(description="Chess Personal Trainer Telegram Bot")
    ap.add_argument("--stockfish", default=STOCKFISH_PATH, help="Path to Stockfish")
    ap.add_argument("--db-path", default=DB_PATH, help="SQLite database path")
    return ap.parse_args()

def run_bot(args):
    """Запуск Telegram бота"""
    if not TELEGRAM_AVAILABLE:
        print("[ERROR] Telegram dependencies not available. Install: pip install python-telegram-bot Pillow")
        return
        
    if not BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN not set")
        return
    
    print("[INFO] Starting Chess Tactics Telegram Bot...")
    print(f"[INFO] Database: {args.db_path}")
    print(f"[INFO] Stockfish: {args.stockfish}")
    
    # Устанавливаем глобальные пути
    global DB_PATH, STOCKFISH_PATH
    DB_PATH = args.db_path
    STOCKFISH_PATH = args.stockfish
    
    # Создаем базу если не существует
    conn = open_db(DB_PATH)
    conn.close()
    
    # Инициализируем бота
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем хэндлеры
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(drill_callback, pattern="^drill_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
    
    async def post_init(application):
        """Инициализация после создания приложения"""
        engine_ok = await bot.init_engine()
        if not engine_ok:
            print("[WARN] Engine not available - /update command will not work")
    
    async def post_stop(application):
        """Очистка при остановке"""
        await bot.cleanup()
    
    app.post_init = post_init
    app.post_stop = post_stop
    
    print("[INFO] Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

def main():
    """Главная функция"""
    args = parse_args()
    run_bot(args)

if __name__ == "__main__":
    main()
