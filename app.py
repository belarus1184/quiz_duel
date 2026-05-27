from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import uuid
import time
import threading
import json
import random
import os
import requests

app = Flask(__name__)
app.secret_key = 'sse_secret'

games = {}

# ==================== НАСТРОЙКА GEMINI ====================
GEMINI_API_KEY = "AIzaSyA6BZuLoLqNys5xpRAtbG5I698SL5UAt3U"  # замените на ваш ключ

def generate_questions_pool():
    """Генерирует 30 вопросов через Gemini API. Возвращает список вопросов (каждый: dict с 'question', 'options', 'correct')."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = (
        "Сгенерируй 30 разнообразных интересных вопросов для викторины. Каждый вопрос должен иметь 4 варианта ответов. "
        "Верни ответ строго в формате JSON: массив из 30 объектов, каждый объект имеет поля:\n"
        "{\"question\": \"текст вопроса\", \"options\": [\"вариант1\", \"вариант2\", \"вариант3\", \"вариант4\"], \"correct\": индекс_правильного_ответа (0-3)}.\n"
        "Вопросы должны быть на русском языке, охватывать разные темы (история, наука, искусство, спорт, география, IT, кино, музыка и т.д.)."
    )
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    headers = {'Content-Type': 'application/json'}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            ai_text = data['candidates'][0]['content']['parts'][0]['text']
            # Извлекаем JSON из ответа (на случай, если модель добавила пояснения)
            start = ai_text.find('[')
            end = ai_text.rfind(']') + 1
            if start != -1 and end != 0:
                questions = json.loads(ai_text[start:end])
                if len(questions) >= 30:
                    return questions[:30]   # берём первые 30
    except Exception as e:
        print(f"Ошибка генерации вопросов через Gemini: {e}")

    # Резервный список (30 простых вопросов) – на случай сбоя API
    fallback = [
        {"question": "Столица Франции?", "options": ["Лондон", "Берлин", "Париж", "Мадрид"], "correct": 2},
        {"question": "2+2?", "options": ["3", "4", "5", "6"], "correct": 1},
        {"question": "Какой цвет получается при смешивании красного и синего?", "options": ["Зелёный", "Фиолетовый", "Оранжевый", "Розовый"], "correct": 1},
        {"question": "Кто написал «Евгений Онегин»?", "options": ["Лермонтов", "Пушкин", "Толстой", "Достоевский"], "correct": 1},
        {"question": "Какой океан самый большой?", "options": ["Атлантический", "Индийский", "Тихий", "Северный Ледовитый"], "correct": 2},
        {"question": "Какая планета самая большая в Солнечной системе?", "options": ["Марс", "Юпитер", "Сатурн", "Нептун"], "correct": 1},
        {"question": "Кто написал картину «Мона Лиза»?", "options": ["Ван Гог", "Пикассо", "Да Винчи", "Рафаэль"], "correct": 2},
        {"question": "Какой газ мы вдыхаем?", "options": ["Кислород", "Углекислый газ", "Азот", "Водород"], "correct": 0},
        {"question": "Какой прибор показывает время?", "options": ["Термометр", "Барометр", "Часы", "Спидометр"], "correct": 2},
        {"question": "Сколько дней в неделе?", "options": ["5", "6", "7", "8"], "correct": 2},
        {"question": "Кто был первым человеком в космосе?", "options": ["Нил Армстронг", "Юрий Гагарин", "Алексей Леонов", "Герман Титов"], "correct": 1},
        {"question": "Как называется столица России?", "options": ["Санкт-Петербург", "Новосибирск", "Москва", "Казань"], "correct": 2},
        {"question": "Какой химический элемент обозначается символом O?", "options": ["Золото", "Кислород", "Осмий", "Олово"], "correct": 1},
        {"question": "Что такое алгоритм в информатике?", "options": ["Программа", "Последовательность действий", "Ошибка в коде", "Тип данных"], "correct": 1},
        {"question": "Какой вид спорта называют «королевой спорта»?", "options": ["Футбол", "Лёгкая атлетика", "Теннис", "Баскетбол"], "correct": 1},
        # Дополним до 30 (повторяем первые несколько, чтобы было 30)
    ]
    # Если недостаточно, дополняем повторами
    while len(fallback) < 30:
        fallback.extend(fallback)
    return fallback[:30]

# ==================== ИГРОВАЯ ЛОГИКА ====================
ROUND_TIME = 25
PAUSE_TIME = 5

def start_round(room):
    game = games[room]
    q_idx = game['q_idx']
    if q_idx >= len(game['questions_pool']):
        end_game(room)
        return
    q = game['questions_pool'][q_idx]
    game['current_question'] = q
    game['correct'] = q['correct']
    game['round_active'] = True
    game['answered'] = [False, False]
    game['player_answers'] = [None, None]
    game['round_start_time'] = time.time()
    game['round_finished'] = False

    def timer():
        time.sleep(ROUND_TIME)
        if room in games and games[room].get('round_active'):
            finish_round(room)
    threading.Thread(target=timer, daemon=True).start()

def finish_round(room):
    game = games[room]
    if not game.get('round_active'):
        return
    game['round_active'] = False
    game['round_finished'] = True

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

    names = game['names']
    messages = []
    for i, ans in enumerate(answers):
        if ans == correct:
            messages.append(f"{names[i]} ответил правильно")
        elif ans is not None and ans != -1:
            messages.append(f"{names[i]} ответил неправильно")
        else:
            messages.append(f"{names[i]} не ответил")

    game['round_results'] = {
        'messages': messages,
        'correct_text': game['current_question']['options'][correct],
        'scores': new_scores
    }

    def next_round():
        time.sleep(PAUSE_TIME)
        if room in games:
            game = games[room]
            game['q_idx'] += 1
            game['round_finished'] = False
            game['round_results'] = None
            start_round(room)
    threading.Thread(target=next_round, daemon=True).start()

def end_game(room):
    game = games[room]
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

    game['game_over'] = True
    game['winner'] = winner
    game['winner_score'] = winner_score
    game['loser_score'] = loser_score
    game['history_table'] = history_table

    def clean():
        time.sleep(10)
        if room in games:
            del games[room]
    threading.Thread(target=clean, daemon=True).start()

# ==================== МАРШРУТЫ ====================
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
    games[room] = {
        'players': [sid],
        'names': [name, None],
        'scores': [0, 0],
        'q_idx': 0,
        'round_active': False,
        'round_finished': False,
        'round_results': None,
        'current_question': None,
        'correct': None,
        'answered': [False, False],
        'player_answers': [None, None],
        'round_start_time': 0,
        'history': [],
        'game_over': False,
        'questions_pool': None   # будет заполнено позже
    }
    return redirect(url_for('wait'))

@app.route('/join', methods=['POST'])
def join():
    name = request.form.get('name', '').strip()
    room = request.form.get('room', '').strip()
    if not name or not room:
        return "Имя и код комнаты обязательны", 400
    if room not in games or len(games[room]['players']) >= 2:
        return "Комната не найдена или занята", 400
    sid = str(uuid.uuid4())
    session['sid'] = sid
    session['room'] = room
    session['name'] = name
    games[room]['players'].append(sid)
    games[room]['names'][1] = name

    if len(games[room]['players']) == 2:
        # Генерируем 30 вопросов при старте игры
        pool = generate_questions_pool()
        games[room]['questions_pool'] = pool
        def start():
            time.sleep(2)
            if room in games:
                start_round(room)
        threading.Thread(target=start, daemon=True).start()
    return redirect(url_for('game'))

@app.route('/wait')
def wait():
    room = session.get('room')
    if not room or room not in games:
        return redirect(url_for('index'))
    if len(games[room]['players']) == 2:
        return redirect(url_for('game'))
    invite_link = url_for('index', _external=True) + '?room=' + room
    return render_template('wait.html', room=room, name=session.get('name', ''), invite_link=invite_link)

@app.route('/check_players')
def check_players():
    room = request.args.get('room')
    if room in games:
        return {'players': len(games[room]['players'])}
    return {'players': 0}

@app.route('/game')
def game():
    sid = session.get('sid')
    name = session.get('name')
    if not sid or not name:
        return redirect(url_for('index'))
    room = session.get('room')
    if not room or room not in games:
        return redirect(url_for('index'))
    player_idx = 0 if games[room]['players'][0] == sid else 1
    session['player_idx'] = player_idx
    names = games[room]['names']
    name1 = names[0] if names[0] else "Игрок 1"
    name2 = names[1] if names[1] else "Игрок 2"
    # total_questions = количество вопросов (30), берём из пула если он уже есть
    q_pool = games[room].get('questions_pool')
    total = len(q_pool) if q_pool else 30
    return render_template('game.html', sid=sid, name=name, player_idx=player_idx, total_questions=total, name1=name1, name2=name2)

@app.route('/state')
def state():
    room = session.get('room')
    if not room or room not in games:
        return jsonify({'error': 'no game'})
    game = games[room]
    state = {
        'players': game['names'],
        'scores': game['scores'],
        'round_active': game['round_active'],
        'round_finished': game.get('round_finished', False),
        'q_idx': game['q_idx'],
        'total_questions': len(game.get('questions_pool', [])),
        'game_over': game.get('game_over', False),
        'winner': game.get('winner'),
        'winner_score': game.get('winner_score'),
        'loser_score': game.get('loser_score'),
        'history': game.get('history_table', [])
    }
    if game['round_active'] and game['current_question']:
        elapsed = time.time() - game['round_start_time']
        remaining = max(0, ROUND_TIME - int(elapsed))
        state['question'] = game['current_question']['question']
        state['options'] = game['current_question']['options']
        state['time_left'] = remaining
        state['correct_index'] = game['correct']
        state['player_answers'] = game['player_answers']
    else:
        state['question'] = None
        state['options'] = []
        state['time_left'] = 0
        state['player_answers'] = [None, None]
    if game.get('round_results') and not game['round_active']:
        state['round_results'] = game['round_results']
    else:
        state['round_results'] = None
    return jsonify(state)

@app.route('/answer', methods=['POST'])
def answer():
    data = request.get_json()
    sid = data.get('sid')
    answer_idx = data.get('answer')
    room = session.get('room')
    if not room or room not in games:
        return jsonify({'error': 'no game'}), 400
    game = games[room]
    if not game.get('round_active'):
        return jsonify({'error': 'round not active'}), 400
    player_idx = 0 if game['players'][0] == sid else 1
    game['player_answers'][player_idx] = answer_idx
    if not game['answered'][player_idx]:
        game['answered'][player_idx] = True
    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
