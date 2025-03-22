from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import sqlite3
import uuid
import json
from database import init_db, add_user, verify_user, get_user, add_meeting, get_meeting, meeting_exists
from gesture_recognition import GestureRecognizer

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize database
init_db()

# Initialize gesture recognizer
gesture_recognizer = GestureRecognizer()

# Active participants (stored in memory but keyed by meeting ID)
active_participants = {}

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if verify_user(email, password):
            user = get_user(email)
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_email'] = user['email']
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials")
    
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        user_added = add_user(name, email, password)
        if user_added:
            return redirect(url_for('login'))
        else:
            return render_template('login.html', error="Email already exists")
    
    return render_template('login.html', signup=True)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    return render_template('dashboard.html', username=session['user_name'])

@app.route('/create_meeting')
def create_meeting():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    meeting_id = str(uuid.uuid4())[:8]
    # Store meeting in database
    add_meeting(meeting_id, session['user_id'])
    
    # Initialize participant tracking for this meeting
    if meeting_id not in active_participants:
        active_participants[meeting_id] = {}
    
    return redirect(url_for('meeting', meeting_id=meeting_id))

@app.route('/join_meeting', methods=['POST'])
def join_meeting():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    meeting_id = request.form.get('meeting_id')
    
    # Check if meeting exists in database
    if meeting_exists(meeting_id):
        return redirect(url_for('meeting', meeting_id=meeting_id))
    else:
        return render_template('dashboard.html', error="Meeting not found", username=session['user_name'])

@app.route('/meeting/<meeting_id>')
def meeting(meeting_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Check if meeting exists in database
    if not meeting_exists(meeting_id):
        # Create meeting if it doesn't exist (for the host)
        add_meeting(meeting_id, session['user_id'])
    
    # Initialize participant tracking for this meeting if not already done
    if meeting_id not in active_participants:
        active_participants[meeting_id] = {}
    
    return render_template('meeting.html', meeting_id=meeting_id, username=session['user_name'], user_id=session['user_id'])

@app.route('/thankyou')
def thankyou():
    meeting_id = request.args.get('meeting_id', '')
    return render_template('thankyou.html', meeting_id=meeting_id)

@app.route('/process_gesture', methods=['POST'])
def process_gesture():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'})
    
    try:
        # Get the frame data from the request
        frame_data = request.json.get('frame')
        meeting_id = request.json.get('meeting_id')
        
        # Process the frame with the gesture recognizer
        gesture, confidence = gesture_recognizer.predict(frame_data)
        
        # Return the result
        return jsonify({
            'success': True,
            'gesture': gesture,
            'confidence': confidence
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# WebSocket event handlers
@socketio.on('join')
def on_join(data):
    meeting_id = data['meeting_id']
    user_id = session.get('user_id')
    username = session.get('user_name')
    
    if not user_id or not username:
        return
    
    join_room(meeting_id)
    
    # Add participant to meeting's active participants
    if meeting_id not in active_participants:
        active_participants[meeting_id] = {}
    
    active_participants[meeting_id][user_id] = {
        'name': username,
        'id': user_id
    }
    
    # Notify other participants
    emit('user_joined', {
        'user_id': user_id,
        'username': username,
        'participants': list(active_participants[meeting_id].values())
    }, to=meeting_id)

@socketio.on('leave')
def on_leave(data):
    meeting_id = data['meeting_id']
    user_id = session.get('user_id')
    username = session.get('user_name')
    
    if not user_id or not username:
        return
    
    leave_room(meeting_id)
    
    # Remove participant from meeting
    if meeting_id in active_participants and user_id in active_participants[meeting_id]:
        del active_participants[meeting_id][user_id]
    
    # If no participants left, clean up (but don't delete from database)
    if meeting_id in active_participants and not active_participants[meeting_id]:
        del active_participants[meeting_id]
    
    # Notify other participants
    emit('user_left', {
        'user_id': user_id,
        'username': username
    }, to=meeting_id)

@socketio.on('gesture_message')
def handle_gesture_message(data):
    meeting_id = data['meeting_id']
    user_id = session.get('user_id')
    username = session.get('user_name')
    message = data['message']
    
    if not user_id or not username:
        return
    
    # Broadcast the message to all participants in the meeting
    emit('new_message', {
        'user_id': user_id,
        'username': username,
        'message': message,
        'type': 'gesture'
    }, to=meeting_id)

@socketio.on('chat_message')
def handle_chat_message(data):
    meeting_id = data['meeting_id']
    user_id = session.get('user_id')
    username = session.get('user_name')
    message = data['message']
    
    if not user_id or not username:
        return
    
    # Broadcast the message to all participants in the meeting
    emit('new_message', {
        'user_id': user_id,
        'username': username,
        'message': message,
        'type': 'chat'
    }, to=meeting_id)

@socketio.on('video_stream')
def handle_video_stream(data):
    meeting_id = data['meeting_id']
    user_id = session.get('user_id')
    stream_data = data['stream']
    
    if not user_id:
        return
    
    # Broadcast the video stream to all participants in the meeting except the sender
    emit('video_stream', {
        'user_id': user_id,
        'stream': stream_data
    }, to=meeting_id, skip_sid=request.sid)

if __name__ == '__main__':
    socketio.run(app, debug=True)
