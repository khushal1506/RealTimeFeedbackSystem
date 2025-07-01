from flask import Flask, request, render_template, url_for, session, redirect, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import google.generativeai as genai
import markdown
from bs4 import BeautifulSoup
from PIL import Image
import os
import re
from datetime import datetime
from html import escape
from functools import wraps

app = Flask(__name__)
app.secret_key = 'dev'

# Configuration
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Gemini AI Configuration
genai.configure(api_key="***YOUR_API_KEY***")

instruction = ("You are a Text extractor, your work is to extract the given text in image and return it in a way that user have written")
instruction_check = ("You are a Assignment checker. You will be provided with image which will contain assignment."
              "Assignment will be handwritten so there may be untidy writting"
            "You have to check for spelling mistakes, grammar (only for literature) and facts to grade the assignment. "
           " Total weightage to a single assignment question is 10 marks or depending on what user specifies so depending on number of mistakes a student makes give him marks and feedback."
           " You have to return text that will look like a human teacher have passed."
           " Also suggest online resources and videos with links which will help in minimizing made mistakes. Pay attention to user input text if given")

model = genai.GenerativeModel("models/gemini-2.0-flash-exp",system_instruction=instruction)

model_text = genai.GenerativeModel("models/gemini-2.0-flash-exp",system_instruction=instruction_check)

def clean_gemini_text(gemini_response):

    html_output = markdown.markdown(gemini_response)  # Convert Markdown to HTML

    soup = BeautifulSoup(html_output, 'html.parser')
    plain_text = soup.get_text()  

    plain_text = plain_text.replace('\n', '\n')  
    plain_text = plain_text.replace("<p>", "").replace("</p>", " ") 
    plain_text = plain_text.replace("<br/>", " ")


    return plain_text

def check_text_with_gemini(image):
    response = model.generate_content([image, "This is the assignment"])
    res = clean_gemini_text(response.text)
    return res

def gem_check(text):
    respon = model_text.generate_content(text)
    resp = clean_gemini_text(respon.text)
    return resp

# Database Operations
def get_db():
    conn = sqlite3.connect('users.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        
        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        
        # Messages table
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                response TEXT NOT NULL,
                image_path TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        conn.commit()

def get_user_id(email):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM users WHERE email = ?', (email,))
        result = c.fetchone()
        return result[0] if result else None
    
# Fetch most recent user chats
@app.route('/get_chat_history')
def get_chat_history():
    if 'email' not in session:
        return redirect(url_for('logInPage'))

    user_id = get_user_id(session['email'])
    if not user_id:
        return jsonify([])

    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT content, response, timestamp
            FROM messages
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT 10
        ''', (user_id,))
        messages = c.fetchall()

    chat_data = [{
        'content': msg['content'],
        'response': msg['response'],
        'timestamp': msg['timestamp']
    } for msg in messages]

    return jsonify(chat_data)

def save_message(user_id, content, response, image_path=None):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO messages (user_id, content, response, image_path, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, content, response, image_path, datetime.now()))
        conn.commit()

def get_user_messages(user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT content, response, image_path, timestamp
            FROM messages
            WHERE user_id = ?
            ORDER BY timestamp DESC
        ''', (user_id,))
        return c.fetchall()

# Utility Functions
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('logInPage'))
        return f(*args, **kwargs)
    return decorated_function

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def convert_urls_to_links(text):
    paragraphs = text.split('\n\n')
    processed_paragraphs = []

    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        processed_lines = []

        for line in lines:
            url_pattern = r'(https?://\S+)'
            def replace_with_link(match):
                url = match.group(1)
                return f'<a href="{escape(url)}" target="_blank" class="resource-link">{escape(url)}</a>'
            line = re.sub(url_pattern, replace_with_link, line)
            processed_lines.append(line)

        processed_paragraphs.append("<br>".join(processed_lines))

    return "<br><br>".join(processed_paragraphs)

# def clean_gemini_text(gemini_response):
#     html_output = markdown.markdown(gemini_response)
#     soup = BeautifulSoup(html_output, 'html.parser')
#     plain_text = soup.get_text()
#     plain_text = plain_text.replace('\n', '\n')
#     plain_text = plain_text.replace("<p>", "\n").replace("</p>", " ")
#     plain_text = plain_text.replace("<br/>", "\n ")
#     return plain_text

# def check_text_with_gemini(image):
#     response = model.generate_content([image, "This is the assignment"])
#     return clean_gemini_text(response.text)

# Routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/chat_main')
@login_required
def chat_main():
    return render_template('main_chat.html')

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if 'file' not in request.files:
        return 'No file uploaded', 400

    file = request.files['file']
    if file.filename == '':
        return 'No selected file', 400
    


    try:
        # Process image
        image = Image.open(file)
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Get feedback
        feedback_text = check_text_with_gemini(image)
        processed_feedback = convert_urls_to_links(feedback_text)
        
        # Save to database
        user_id = get_user_id(session['email'])
        additional_text = request.form.get('text', '')
        save_message(user_id, additional_text, processed_feedback, filepath)
        
        return processed_feedback
    except Exception as e:
        return f"Error processing image: {e}", 500
        
@app.route('/uptext',methods=['POST'])
def uptext():
    texted=request.data.decode('utf-8')
    if texted:
        respo = gem_check(texted)
        return respo
    else:
        return "NO DATA RECEIVED", 400

@app.route('/signInPage')
def signInPage():
    return render_template('SignIn.html')

@app.route('/logInPage')
def logInPage():
    return render_template('LogIn.html')

@app.route('/LogIn', methods=['POST'])
def LogIn():
    email = request.form['email']
    password = request.form['password']

    if not is_valid_email(email):
        flash('Invalid email format', 'error')
        return redirect(url_for('logInPage'))

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = c.fetchone()

    if user and check_password_hash(user[2], password):
        session['email'] = email
        return redirect(url_for('chat_main'))
    
    flash('Invalid email or password', 'error')
    return redirect(url_for('logInPage'))

@app.route('/SignIn', methods=['POST'])
def SignIn():
    email = request.form['email']
    password = request.form['password']
    confirm_password = request.form['confirm_password']
    username = request.form['username']

    if not is_valid_email(email):
        flash('Invalid email format', 'error')
        return redirect(url_for('signInPage'))

    if len(password) < 6:
        flash('Password must be at least 6 characters long', 'error')
        return redirect(url_for('signInPage'))

    if password != confirm_password:
        flash('Passwords do not match', 'error')
        return redirect(url_for('signInPage'))

    try:
        with get_db() as conn:
            c = conn.cursor()
            hashed_password = generate_password_hash(password)
            c.execute('INSERT INTO users (email, password) VALUES (?, ?)', 
                     (email, hashed_password))
            conn.commit()
        
        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('logInPage'))
    except sqlite3.IntegrityError:
        flash('Email already registered', 'error')
        return redirect(url_for('signInPage'))

@app.route('/logout')
def logout():
    session.pop('email', None)
    flash("You have been logged out.")
    return redirect(url_for('logInPage'))

@app.route('/fetch')
@login_required
def fetch():
    return render_template('fetch.html')

@app.route('/history')
@login_required
def history():
    return render_template('history.html')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)