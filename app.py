from flask import Flask, render_template, request, session, redirect, url_for, jsonify, Response
import uuid
import time
import threading
import json
import random
import os

app = Flask(__name__)
app.secret_key = 'sse_secret'

games = {}
event_streams = {}

def load_questions():
    if not os.path.exists('questions.json'):
        return [
            {"question": "Столица Франции?", "options": ["Лондон", "Берлин", "Париж", "Мадрид"], "correct": 2},
            {"question": "2+2?", "options": ["3", "4", "5", "6"], "correct": 1}
        ]
    for encoding in ['utf-8', 'cp1251', 'latin-1']:
        try:
            with open('questions.json', 'r', encoding=encoding) as f:
                qlist = json.load(f)
                random.shuffle(qlist)
                print(f"Вопросы загружены (кодировка {encoding})")
                return qlist
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    print("Не удалось загрузить questions.json, использую резервный список")
    return [
        {"question": "Столица Франции?", "options": ["Лондон", "Берлин", "Париж", "Мадрид"], "correct": 2},
        {"question": "2+2?", "options": ["3", "4", "5", "6"], "correct": 1}
    ]

QUESTIONS = load_questions()
ROUND_TIME = 25
PAUSE_TIME = 5

def send_event(sid, event, data):
    if sid not in event_streams:
        event_streams[sid] = []
    event_streams[sid].append((event, data))

def broadcast(room, event, data):
    if room not in games:
        return
    for sid in games[room]['players']:
        send_event(sid, event, data)

def start_round(room):
    game = games[room]
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
    broadcast(room, 'new_question', {
        'index': q_idx,
        'text': q['question'],
        'options': q['options'],
        'time': ROUND_TIME,
        'total': len(QUESTIONS)
    })
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
    broadcast(room, 'round_result', {
        'messages': messages,
        'scores': new_scores,
        'names': names,
        'correct_index': correct,
        'correct_text': game['current_question']['options'][correct]
    })
    def next_round():
        time.sleep(PAUSE_TIME)
        if room in games:
            game = games[room]
            game['q_idx'] += 1
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
        if room in games:
            del games[room]
    threading.Thread(target=clean, daemon=True).start()

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
        'current_question': None,
        'correct': None,
        'answered': [False, False],
        'player_answers': [None, None],
        'round_start_time': 0,
        'history': []
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
    return render_template('game.html', sid=sid, name=name, player_idx=player_idx, total_questions=len(QUESTIONS), name1=name1, name2=name2)

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
        'q_idx': game['q_idx'],
        'total_questions': len(QUESTIONS),
        'game_over': game.get('game_over', False),
        'winner': game.get('winner'),
        'winner_score': game.get('winner_score'),
        'loser_score': game.get('loser_score')
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
    if game['answered'][player_idx]:
        return jsonify({'error': 'already answered'}), 400
    game['player_answers'][player_idx] = answer_idx
    game['answered'][player_idx] = True
    if all(game['answered']):
        finish_round(room)
    return jsonify({'ok': True})

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
