from flask import Flask, render_template, request, session, redirect, url_for, Response, jsonify
import uuid
import time
import threading
import json
import random
import sqlite3
import os

app = Flask(__name__)
app.secret_key = 'sse_secret'

# ========== НАСТРОЙКА БАЗЫ ДАННЫХ ==========
DATABASE = 'quiz.db'

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS games (
                room TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated REAL DEFAULT (strftime('%s', 'now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_updated ON games(updated)')

def save_game(room, game_data):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('REPLACE INTO games (room, data) VALUES (?, ?)', 
                     (room, json.dumps(game_data)))

def load_game(room):
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.execute('SELECT data FROM games WHERE room = ?', (room,))
        row = cur.fetchone()
        if row:
            return json.loads(row[0])
    return None

def delete_game(room):
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('DELETE FROM games WHERE room = ?', (room,))

def cleanup_old_games():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute('DELETE FROM games WHERE updated < strftime("%s", "now", "-2 hours")')

init_db()
cleanup_old_games()

# ========== ЗАГРУЗКА ВОПРОСОВ ==========
def load_questions():
    if not os.path.exists('questions.json'):
        return [
            {"question": "Столица Франции?", "options": ["Лондон", "Берлин", "Париж", "Мадрид"], "correct": 2},
            {"question": "2+2?", "options": ["3", "4", "5", "6"], "correct": 1}
        ]
    with open('questions.json', 'r', encoding='utf-8') as f:
        qlist = json.load(f)
        random.shuffle(qlist)
        return qlist

QUESTIONS = load_questions()
ROUND_TIME = 25
PAUSE_TIME = 5

# ========== ИГРОВАЯ ЛОГИКА ==========
event_streams = {}

def send_event(sid, event, data):
    if sid not in event_streams:
        event_streams[sid] = []
    event_streams[sid].append((event, data))

def broadcast(room, event, data):
    game = load_game(room)
    if not game:
        return
    for sid in game['players']:
        send_event(sid, event, data)

def start_round(room):
    game = load_game(room)
    if not game:
        return
    q_idx = game['q_idx']
    if q_idx >= len(QUESTIONS):
        end_game(room)
        return
    q = QUESTIONS[q_idx]
    game['current_question'] = q
    game['correct'] = q['correct']
    game['round_active'] = True
    game['answered'] = [False, False]
    game['player_answers'] = [None, None]
    game['round_start_time'] = time.time()
    save_game(room, game)
    
    broadcast(room, 'new_question', {
        'index': q_idx,
        'text': q['question'],
        'options': q['options'],
        'time': ROUND_TIME,
        'total': len(QUESTIONS)
    })
    
    def timer():
        time.sleep(ROUND_TIME)
        game_check = load_game(room)
        if game_check and game_check.get('round_active'):
            finish_round(room)
    threading.Thread(target=timer, daemon=True).start()

def finish_round(room):
    game = load_game(room)
    if not game or not game.get('round_active'):
        return
    correct = game['correct']
    answers = game['player_answers']
    new_scores = game['scores'][:]
    for i, ans in enumerate(answers):
        if ans == correct:
            new_scores[i] += 1
    game['scores'] = new_scores
    if 'history' not in game:
        game['history'] = []
    game['history'].append({
        'question': game['current_question']['question'],
        'answers': answers.copy(),
        'correct': correct
    })
    game['round_active'] = False
    save_game(room, game)
    
    names = game['names']
    messages = []
    for i, ans in enumerate(answers):
        if ans == correct:
            messages.append(f"{names[i]} ответил правильно")
        elif ans is not None and ans != -1:
            messages.append(f"{names[i]} ответил неправильно")
        else:
            messages.append(f"{names[i]} не ответил")
    
    broadcast(room, 'round_result', {
        'messages': messages,
        'scores': new_scores,
        'names': names,
        'correct_index': correct,
        'correct_text': game['current_question']['options'][correct]
    })
    
    def next_round():
        time.sleep(PAUSE_TIME)
        game_check = load_game(room)
        if game_check:
            game_check['q_idx'] += 1
            save_game(room, game_check)
            start_round(room)
    threading.Thread(target=next_round, daemon=True).start()

def end_game(room):
    game = load_game(room)
    if not game:
        return
    scores = game['scores']
    names = game['names']
    if scores[0] > scores[1]:
        winner = names[0]
        winner_score = scores[0]
        loser_score = scores[1]
    elif scores[1] > scores[0]:
        winner = names[1]
        winner_score = scores[1]
        loser_score = scores[0]
    else:
        winner = None
        winner_score = scores[0]
        loser_score = scores[1]
    
    history_table = []
    for h in game.get('history', []):
        history_table.append({
            'question': h['question'],
            'answer1': h['answers'][0],
            'answer2': h['answers'][1],
            'correct': h['correct']
        })
    
    broadcast(room, 'game_over', {
        'winner': winner,
        'winner_score': winner_score,
        'loser_score': loser_score,
        'scores': scores,
        'names': names,
        'history': history_table
    })
    
    def clean():
        time.sleep(10)
        delete_game(room)
    threading.Thread(target=clean, daemon=True).start()

# ========== МАРШРУТЫ ==========
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['POST'])
def create():
    name = request.form.get('name', '').strip()
    if not name:
        return "Имя обязательно", 400
    room = str(uuid.uuid4())[:6]
    sid = str(uuid.uuid4())
    session['sid'] = sid
    session['room'] = room
    session['name'] = name
    game = {
        'players': [sid],
        'names': [name, None],
        'scores': [0, 0],
        'q_idx': 0,
        'round_active': False,
        'current_question': None,
        'correct': None,
        'answered': [False, False],
        'player_answers': [None, None],
        'round_start_time': 0,
        'history': []
    }
    save_game(room, game)
    print(f"[CREATE] room={room}, name={name}")
    return redirect(url_for('wait'))

@app.route('/join', methods=['POST'])
def join():
    name = request.form.get('name', '').strip()
    room = request.form.get('room', '').strip()
    if not name or not room:
        return "Имя и код комнаты обязательны", 400
    game = load_game(room)
    if not game or len(game['players']) >= 2:
        return "Комната не найдена или занята", 400
    sid = str(uuid.uuid4())
    session['sid'] = sid
    session['room'] = room
    session['name'] = name
    game['players'].append(sid)
    game['names'][1] = name
    save_game(room, game)
    print(f"[JOIN] room={room}, name={name}, players={len(game['players'])}")
    if len(game['players']) == 2:
        def start():
            time.sleep(2)
            if load_game(room):
                start_round(room)
        threading.Thread(target=start, daemon=True).start()
    return redirect(url_for('game'))

@app.route('/wait')
def wait():
    room = session.get('room')
    if not room:
        return redirect(url_for('index'))
    game = load_game(room)
    if not game:
        return redirect(url_for('index'))
    if len(game['players']) == 2:
        return redirect(url_for('game'))
    return render_template('wait.html', room=room, name=session.get('name', ''))

@app.route('/game')
def game():
    sid = session.get('sid')
    name = session.get('name')
    if not sid or not name:
        return redirect(url_for('index'))
    room = session.get('room')
    if not room:
        return redirect(url_for('index'))
    game = load_game(room)
    if not game:
        return redirect(url_for('index'))
    player_idx = 0 if game['players'][0] == sid else 1
    session['player_idx'] = player_idx
    names = game['names']
    name1 = names[0] if names[0] else "Игрок 1"
    name2 = names[1] if names[1] else "Игрок 2"
    return render_template('game.html', sid=sid, name=name, player_idx=player_idx, total_questions=len(QUESTIONS), name1=name1, name2=name2)

@app.route('/check_players')
def check_players():
    room = request.args.get('room')
    game = load_game(room)
    if game:
        return {'players': len(game['players'])}
    return {'players': 0}

@app.route('/stream')
def stream():
    sid = request.args.get('sid')
    if not sid:
        return "no sid", 400
    def generate():
        while True:
            time.sleep(0.1)
            if sid in event_streams and event_streams[sid]:
                event, data = event_streams[sid].pop(0)
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
    return Response(generate(), mimetype="text/event-stream")

@app.route('/answer', methods=['POST'])
def answer():
    data = request.get_json()
    sid = data.get('sid')
    answer_idx = data.get('answer')
    room = session.get('room')
    if not room:
        return jsonify({'error': 'no game'}), 400
    game = load_game(room)
    if not game or not game.get('round_active'):
        return jsonify({'error': 'round not active'}), 400
    player_idx = 0 if game['players'][0] == sid else 1
    if game['answered'][player_idx]:
        return jsonify({'error': 'already answered'}), 400
    game['player_answers'][player_idx] = answer_idx
    game['answered'][player_idx] = True
    save_game(room, game)
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
