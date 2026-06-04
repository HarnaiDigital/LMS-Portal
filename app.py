# app.py - Complete LMS Application with all fixes
import sqlite3
import hashlib
import os
import secrets
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort, send_from_directory
from werkzeug.utils import secure_filename

# Try to import DNS validator (optional)
try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False
    print("Note: Install dnspython for email domain verification: pip install dnspython")

app = Flask(__name__)

# Fix for Railway - ensure templates are found
app = Flask(__name__, template_folder='templates', static_folder='static')

# Create static folder if not exists
os.makedirs('static', exist_ok=True)
app.secret_key = 'your-secret-key-here-change-in-production'

# File upload configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'zip', 'rar', 'jpg', 'png', 'pptx', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database configuration
DATABASE = 'lms.db'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def validate_email_domain(email, role):
    """Validate email domain based on role"""
    if role == 'teacher':
        if not email.endswith('@iiu.edu.pk'):
            return False, "Teachers must use @iiu.edu.pk email address"
    elif role == 'student':
        if not email.endswith('@student.iiu.edu.pk'):
            return False, "Students must use @student.iiu.edu.pk email address"
    return True, "Valid email"

def validate_email(email):
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return False, "Invalid email format"
    return True, "Valid email"

def send_sms_alert(phone_number, message):
    print(f"\n=== SMS ALERT ===\nTo: {phone_number}\nMessage: {message}\n================\n")
    return True

def log_activity(user_id, action):
    db = get_db()
    db.execute('INSERT INTO activity_logs (user_id, action) VALUES (?, ?)', (user_id, action))
    db.commit()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please login to access this page.', 'warning')
                return redirect(url_for('login'))
            user_role = session.get('role')
            if user_role not in roles:
                flash('You do not have permission to access this page.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def create_student_notification(student_id, title, content, type, reference_id=None):
    db = get_db()
    db.execute('''
        INSERT INTO student_notifications (student_id, title, content, type, reference_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (student_id, title, content, type, reference_id))
    db.commit()

def notify_all_students(title, content, type, reference_id=None):
    db = get_db()
    students = db.execute('SELECT id FROM users WHERE role = "student"').fetchall()
    for student in students:
        create_student_notification(student['id'], title, content, type, reference_id)

def init_db():
    db = get_db()
    cursor = db.cursor()

    # Create tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            mobile TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student', 'teacher', 'admin')),
            reset_token TEXT,
            reset_token_expiry TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            teacher_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES users (id),
            FOREIGN KEY (course_id) REFERENCES courses (id),
            UNIQUE(student_id, course_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS course_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            due_date TIMESTAMP,
            max_points INTEGER DEFAULT 100 CHECK(max_points <= 100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            submission_text TEXT,
            file_path TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            grade INTEGER CHECK(grade <= 100),
            graded_at TIMESTAMP,
            feedback TEXT,
            FOREIGN KEY (assignment_id) REFERENCES assignments (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            total_points INTEGER DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            points INTEGER DEFAULT 10,
            FOREIGN KEY (quiz_id) REFERENCES quizzes (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            score INTEGER,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (quiz_id) REFERENCES quizzes (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            posted_by INTEGER NOT NULL,
            audience TEXT DEFAULT 'teachers',
            posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (posted_by) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            date DATE NOT NULL,
            status TEXT CHECK(status IN ('present', 'absent')),
            FOREIGN KEY (student_id) REFERENCES users (id),
            FOREIGN KEY (course_id) REFERENCES courses (id),
            UNIQUE(student_id, course_id, date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            parent_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (parent_id) REFERENCES discussions (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL,
            reference_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read INTEGER DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
    ''')

    # Create default admin with correct domain
    admin = cursor.execute("SELECT * FROM users WHERE email = 'admin@iiu.edu.pk'").fetchone()
    if not admin:
        password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute('INSERT INTO users (name, email, mobile, password_hash, role) VALUES (?, ?, ?, ?, ?)',
                      ('System Admin', 'admin@iiu.edu.pk', '9999999999', password_hash, 'admin'))

    # Create demo teacher with correct domain
    teacher = cursor.execute("SELECT * FROM users WHERE email = 'teacher@iiu.edu.pk'").fetchone()
    if not teacher:
        password_hash = hashlib.sha256('teacher123'.encode()).hexdigest()
        cursor.execute('INSERT INTO users (name, email, mobile, password_hash, role) VALUES (?, ?, ?, ?, ?)',
                      ('Demo Teacher', 'teacher@iiu.edu.pk', '8888888888', password_hash, 'teacher'))

    # Create demo student with correct domain
    student = cursor.execute("SELECT * FROM users WHERE email = 'student@student.iiu.edu.pk'").fetchone()
    if not student:
        password_hash = hashlib.sha256('student123'.encode()).hexdigest()
        cursor.execute('INSERT INTO users (name, email, mobile, password_hash, role) VALUES (?, ?, ?, ?, ?)',
                      ('Demo Student', 'student@student.iiu.edu.pk', '7777777777', password_hash, 'student'))

    # Create demo course
    teacher_id = cursor.execute("SELECT id FROM users WHERE email = 'teacher@iiu.edu.pk'").fetchone()
    if teacher_id:
        course = cursor.execute("SELECT * FROM courses WHERE title = 'Introduction to Python'").fetchone()
        if not course:
            cursor.execute('INSERT INTO courses (title, description, teacher_id) VALUES (?, ?, ?)',
                          ('Introduction to Python', 'Learn Python programming from scratch', teacher_id['id']))

            student_id = cursor.execute("SELECT id FROM users WHERE email = 'student@student.iiu.edu.pk'").fetchone()
            course_id = cursor.execute("SELECT id FROM courses WHERE title = 'Introduction to Python'").fetchone()
            if student_id and course_id:
                cursor.execute('INSERT OR IGNORE INTO enrollments (student_id, course_id) VALUES (?, ?)',
                              (student_id['id'], course_id['id']))

    db.commit()

# ==================== ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        mobile = request.form['mobile']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        role = request.form['role']

        if password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('signup'))

        # Validate email format
        valid, msg = validate_email(email)
        if not valid:
            flash(msg, 'danger')
            return redirect(url_for('signup'))

        # Validate email domain based on role
        valid, msg = validate_email_domain(email, role)
        if not valid:
            flash(msg, 'danger')
            return redirect(url_for('signup'))

        db = get_db()
        existing = db.execute('SELECT * FROM users WHERE email = ? OR mobile = ?', (email, mobile)).fetchone()
        if existing:
            flash('Email or Mobile number already registered!', 'danger')
            return redirect(url_for('signup'))

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        db.execute('INSERT INTO users (name, email, mobile, password_hash, role) VALUES (?, ?, ?, ?, ?)',
                  (name, email, mobile, password_hash, role))
        db.commit()

        send_sms_alert(mobile, f"Welcome {name}! Your LMS account has been created.")
        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ? AND password_hash = ?', (email, password_hash)).fetchone()

        if user:
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['role'] = user['role']
            session['email'] = user['email']
            session['mobile'] = user['mobile']
            log_activity(user['id'], "User logged in")
            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password!', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_activity(session['user_id'], "User logged out")
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user:
            token = secrets.token_urlsafe(32)
            expiry = (datetime.now() + timedelta(hours=1)).isoformat()
            db.execute('UPDATE users SET reset_token = ?, reset_token_expiry = ? WHERE id = ?',
                      (token, expiry, user['id']))
            db.commit()
            reset_link = f"http://localhost:5000/reset-password/{token}"
            send_sms_alert(user['mobile'], f"Password reset link: {reset_link}")
            flash('Password reset link sent to your mobile.', 'info')
        else:
            flash('Email not found!', 'danger')
        return redirect(url_for('login'))

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE reset_token = ? AND reset_token_expiry > ?',
                     (token, datetime.now().isoformat())).fetchone()

    if not user:
        flash('Invalid or expired reset token!', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('reset_password', token=token))

        password_hash = hashlib.sha256(new_password.encode()).hexdigest()
        db.execute('UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expiry = NULL WHERE id = ?',
                  (password_hash, user['id']))
        db.commit()

        send_sms_alert(user['mobile'], "Your password has been changed.")
        flash('Password reset successful!', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

@app.route('/dashboard')
@login_required
def dashboard():
    role = session['role']
    if role == 'student':
        return redirect(url_for('student_dashboard'))
    elif role == 'teacher':
        return redirect(url_for('teacher_dashboard'))
    elif role == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('index'))

@app.route('/student-dashboard')
@login_required
@role_required('student')
def student_dashboard():
    user_id = session['user_id']
    db = get_db()

    enrolled_courses = db.execute('''
        SELECT c.*, u.name as teacher_name
        FROM courses c
        JOIN enrollments e ON c.id = e.course_id
        JOIN users u ON c.teacher_id = u.id
        WHERE e.student_id = ?
    ''', (user_id,)).fetchall()

    pending_assignments = db.execute('''
        SELECT a.*, c.title as course_title, s.id as submission_id
        FROM assignments a
        JOIN courses c ON a.course_id = c.id
        JOIN enrollments e ON c.id = e.course_id
        LEFT JOIN submissions s ON a.id = s.assignment_id AND s.student_id = ?
        WHERE e.student_id = ? AND (s.id IS NULL OR s.grade IS NULL)
        AND (a.due_date IS NULL OR a.due_date > datetime('now'))
    ''', (user_id, user_id)).fetchall()

    # Get student-specific notifications
    notifications = db.execute('''
        SELECT * FROM student_notifications
        WHERE student_id = ?
        ORDER BY created_at DESC LIMIT 10
    ''', (user_id,)).fetchall()

    # Get announcements
    announcements = db.execute('''
        SELECT a.*, u.name as posted_by_name
        FROM announcements a
        JOIN users u ON a.posted_by = u.id
        WHERE a.audience = 'teachers' OR a.audience = 'all'
        ORDER BY a.posted_at DESC LIMIT 5
    ''').fetchall()

    return render_template('student_dashboard.html',
                         enrolled_courses=enrolled_courses,
                         pending_assignments=pending_assignments,
                         announcements=announcements,
                         notifications=notifications)

@app.route('/teacher-dashboard')
@login_required
@role_required('teacher')
def teacher_dashboard():
    user_id = session['user_id']
    db = get_db()

    courses = db.execute('SELECT * FROM courses WHERE teacher_id = ?', (user_id,)).fetchall()

    # Get teacher-only announcements
    teacher_announcements = db.execute('''
        SELECT a.*, u.name as posted_by_name
        FROM announcements a
        JOIN users u ON a.posted_by = u.id
        WHERE a.audience = 'teachers' OR a.audience = 'all'
        ORDER BY a.posted_at DESC LIMIT 10
    ''').fetchall()

    recent_submissions = db.execute('''
        SELECT s.*, a.title as assignment_title, c.title as course_title, u.name as student_name, s.file_path
        FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        JOIN users u ON s.student_id = u.id
        WHERE c.teacher_id = ? AND s.grade IS NULL
        ORDER BY s.submitted_at DESC LIMIT 10
    ''', (user_id,)).fetchall()

    return render_template('teacher_dashboard.html',
                         courses=courses,
                         recent_submissions=recent_submissions,
                         teacher_announcements=teacher_announcements)

@app.route('/admin-dashboard')
@login_required
@role_required('admin')
def admin_dashboard():
    db = get_db()

    total_users = db.execute('SELECT COUNT(*) as count FROM users').fetchone()['count']
    total_students = db.execute('SELECT COUNT(*) as count FROM users WHERE role = "student"').fetchone()['count']
    total_teachers = db.execute('SELECT COUNT(*) as count FROM users WHERE role = "teacher"').fetchone()['count']
    total_courses = db.execute('SELECT COUNT(*) as count FROM courses').fetchone()['count']
    total_assignments = db.execute('SELECT COUNT(*) as count FROM assignments').fetchone()['count']

    # Get all courses with teacher names
    all_courses = db.execute('''
        SELECT c.*, u.name as teacher_name, u.id as teacher_id
        FROM courses c
        JOIN users u ON c.teacher_id = u.id
        ORDER BY c.created_at DESC
    ''').fetchall()

    # Get all teachers with their names
    all_teachers = db.execute('SELECT id, name, email FROM users WHERE role = "teacher"').fetchall()

    recent_activities = db.execute('''
        SELECT al.*, u.name as user_name
        FROM activity_logs al
        JOIN users u ON al.user_id = u.id
        ORDER BY al.timestamp DESC LIMIT 20
    ''').fetchall()

    all_users = db.execute('SELECT id, name, email, mobile, role FROM users').fetchall()

    return render_template('admin_dashboard.html',
                         total_users=total_users,
                         total_students=total_students,
                         total_teachers=total_teachers,
                         total_courses=total_courses,
                         total_assignments=total_assignments,
                         recent_activities=recent_activities,
                         all_users=all_users,
                         all_courses=all_courses,
                         all_teachers=all_teachers)

# ADMIN USER MANAGEMENT
@app.route('/admin/delete-user/<int:user_id>')
@login_required
@role_required('admin')
def delete_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user and user['role'] != 'admin':
        db.execute('DELETE FROM users WHERE id = ?', (user_id,))
        db.commit()
        log_activity(session['user_id'], f"Deleted user: {user['email']}")
        flash('User deleted successfully!', 'success')
    else:
        flash('Cannot delete admin user!', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-course/<int:course_id>')
@login_required
@role_required('admin')
def delete_course(course_id):
    db = get_db()
    course = db.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
    if course:
        # Delete related records
        db.execute('DELETE FROM enrollments WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM course_materials WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM assignments WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM submissions WHERE assignment_id IN (SELECT id FROM assignments WHERE course_id = ?)', (course_id,))
        db.execute('DELETE FROM quizzes WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM quiz_attempts WHERE quiz_id IN (SELECT id FROM quizzes WHERE course_id = ?)', (course_id,))
        db.execute('DELETE FROM attendance WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM discussions WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM courses WHERE id = ?', (course_id,))
        db.commit()
        log_activity(session['user_id'], f"Deleted course: {course['title']}")
        flash('Course deleted successfully!', 'success')
    else:
        flash('Course not found!', 'danger')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add-user', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def add_user():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        mobile = request.form['mobile']
        password = request.form['password']
        role = request.form['role']

        valid, msg = validate_email(email)
        if not valid:
            flash(msg, 'danger')
            return redirect(url_for('add_user'))

        # Validate domain for teacher/student
        if role in ['teacher', 'student']:
            valid, msg = validate_email_domain(email, role)
            if not valid:
                flash(msg, 'danger')
                return redirect(url_for('add_user'))

        db = get_db()
        existing = db.execute('SELECT * FROM users WHERE email = ? OR mobile = ?', (email, mobile)).fetchone()
        if existing:
            flash('Email or Mobile already registered!', 'danger')
            return redirect(url_for('add_user'))

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        db.execute('INSERT INTO users (name, email, mobile, password_hash, role) VALUES (?, ?, ?, ?, ?)',
                  (name, email, mobile, password_hash, role))
        db.commit()
        log_activity(session['user_id'], f"Added user: {email}")
        flash('User added successfully!', 'success')
        return redirect(url_for('admin_dashboard'))

    return render_template('add_user.html')

@app.route('/admin/view-user/<int:user_id>')
@login_required
@role_required('admin')
def view_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        flash('User not found!', 'danger')
        return redirect(url_for('admin_dashboard'))

    enrollments = []
    if user['role'] == 'student':
        enrollments = db.execute('''
            SELECT c.title, c.id FROM enrollments e
            JOIN courses c ON e.course_id = c.id
            WHERE e.student_id = ?
        ''', (user_id,)).fetchall()

    return render_template('view_user.html', user=user, enrollments=enrollments)

# ==================== COURSE MANAGEMENT (FIXED) ====================

@app.route('/create-course', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def create_course():
    db = get_db()
    
    # Get teachers for dropdown (only if admin)
    teachers = []
    if session['role'] == 'admin':
        teachers = db.execute('SELECT id, name, email FROM users WHERE role = "teacher" ORDER BY name').fetchall()
    
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        
        # Determine teacher_id
        if session['role'] == 'admin':
            teacher_id = request.form.get('teacher_id')
            if not teacher_id:
                flash('Please select a teacher for this course!', 'danger')
                return redirect(url_for('create_course'))
        else:
            teacher_id = session['user_id']
        
        # Insert course
        db.execute('INSERT INTO courses (title, description, teacher_id) VALUES (?, ?, ?)',
                  (title, description, teacher_id))
        db.commit()
        
        # Get teacher name for flash message
        teacher = db.execute('SELECT name FROM users WHERE id = ?', (teacher_id,)).fetchone()
        
        log_activity(session['user_id'], f"Created course: {title} assigned to {teacher['name']}")
        flash(f'✅ Course "{title}" created successfully and assigned to {teacher["name"]}!', 'success')
        
        if session['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('teacher_dashboard'))
    
    return render_template('create_course.html', teachers=teachers, role=session['role'])

@app.route('/course/<int:course_id>')
@login_required
def view_course(course_id):
    db = get_db()
    course = db.execute('SELECT c.*, u.name as teacher_name FROM courses c JOIN users u ON c.teacher_id = u.id WHERE c.id = ?', (course_id,)).fetchone()

    if not course:
        flash('Course not found!', 'danger')
        return redirect(url_for('dashboard'))

    is_enrolled = False
    if session['role'] == 'student':
        enrollment = db.execute('SELECT * FROM enrollments WHERE student_id = ? AND course_id = ?',
                               (session['user_id'], course_id)).fetchone()
        is_enrolled = bool(enrollment)

    materials = db.execute('SELECT * FROM course_materials WHERE course_id = ?', (course_id,)).fetchall()
    assignments = db.execute('SELECT * FROM assignments WHERE course_id = ?', (course_id,)).fetchall()
    quizzes = db.execute('SELECT * FROM quizzes WHERE course_id = ?', (course_id,)).fetchall()
    discussions = db.execute('''
        SELECT d.*, u.name as user_name
        FROM discussions d
        JOIN users u ON d.user_id = u.id
        WHERE d.course_id = ? AND d.parent_id IS NULL
        ORDER BY d.created_at DESC
    ''', (course_id,)).fetchall()

    return render_template('course_detail.html',
                         course=course,
                         materials=materials,
                         assignments=assignments,
                         quizzes=quizzes,
                         discussions=discussions,
                         is_enrolled=is_enrolled)

@app.route('/browse-courses')
@login_required
@role_required('student')
def browse_courses():
    db = get_db()
    enrolled = db.execute('SELECT course_id FROM enrollments WHERE student_id = ?', (session['user_id'],)).fetchall()
    enrolled_ids = [e['course_id'] for e in enrolled]

    if enrolled_ids:
        placeholders = ','.join('?' * len(enrolled_ids))
        available_courses = db.execute(f'''
            SELECT c.*, u.name as teacher_name
            FROM courses c JOIN users u ON c.teacher_id = u.id
            WHERE c.id NOT IN ({placeholders})
        ''', enrolled_ids).fetchall()
    else:
        available_courses = db.execute('''
            SELECT c.*, u.name as teacher_name
            FROM courses c JOIN users u ON c.teacher_id = u.id
        ''').fetchall()

    return render_template('browse_courses.html', courses=available_courses)

@app.route('/enroll-course/<int:course_id>')
@login_required
@role_required('student')
def enroll_course_route(course_id):
    db = get_db()
    existing = db.execute('SELECT * FROM enrollments WHERE student_id = ? AND course_id = ?',
                         (session['user_id'], course_id)).fetchone()
    if not existing:
        db.execute('INSERT INTO enrollments (student_id, course_id) VALUES (?, ?)',
                  (session['user_id'], course_id))
        db.commit()
        log_activity(session['user_id'], f"Enrolled in course {course_id}")
        flash('Successfully enrolled in course!', 'success')
    else:
        flash('Already enrolled!', 'warning')
    return redirect(url_for('student_dashboard'))

@app.route('/add-material/<int:course_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin')
def add_material(course_id):
    title = request.form['title']
    content = request.form['content']

    db = get_db()
    db.execute('INSERT INTO course_materials (course_id, title, content) VALUES (?, ?, ?)',
              (course_id, title, content))
    db.commit()

    flash('Study material added!', 'success')
    return redirect(url_for('view_course', course_id=course_id))

# ASSIGNMENTS
@app.route('/create-assignment/<int:course_id>', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def create_assignment(course_id):
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        due_date = request.form.get('due_date')
        max_points = int(request.form.get('max_points', 100))

        # Ensure max_points doesn't exceed 100
        if max_points > 100:
            max_points = 100
            flash('Maximum points set to 100 (cannot exceed 100)', 'warning')

        db = get_db()
        cursor = db.execute('''
            INSERT INTO assignments (course_id, title, description, due_date, max_points)
            VALUES (?, ?, ?, ?, ?)
        ''', (course_id, title, description, due_date, max_points))
        assignment_id = cursor.lastrowid
        db.commit()

        log_activity(session['user_id'], f"Created assignment: {title}")

        # Notify all enrolled students
        students = db.execute('''
            SELECT DISTINCT u.id, u.name FROM users u
            JOIN enrollments e ON u.id = e.student_id
            WHERE e.course_id = ?
        ''', (course_id,)).fetchall()

        for student in students:
            create_student_notification(student['id'], f"New Assignment: {title}",
                                       f"A new assignment '{title}' has been posted in your course.",
                                       'assignment', assignment_id)

        flash('Assignment created! Students have been notified.', 'success')
        return redirect(url_for('view_course', course_id=course_id))

    return render_template('create_assignment.html', course_id=course_id)

@app.route('/assignment/<int:assignment_id>')
@login_required
def view_assignment(assignment_id):
    db = get_db()
    assignment = db.execute('''
        SELECT a.*, c.title as course_title, c.id as course_id
        FROM assignments a
        JOIN courses c ON a.course_id = c.id
        WHERE a.id = ?
    ''', (assignment_id,)).fetchone()

    if not assignment:
        flash('Assignment not found!', 'danger')
        return redirect(url_for('dashboard'))

    submission = None
    if session['role'] == 'student':
        submission = db.execute('SELECT * FROM submissions WHERE assignment_id = ? AND student_id = ?',
                               (assignment_id, session['user_id'])).fetchone()

    submissions = []
    if session['role'] in ['teacher', 'admin']:
        submissions = db.execute('''
            SELECT s.*, u.name as student_name
            FROM submissions s
            JOIN users u ON s.student_id = u.id
            WHERE s.assignment_id = ?
        ''', (assignment_id,)).fetchall()

    return render_template('assignment_detail.html',
                         assignment=assignment,
                         submission=submission,
                         submissions=submissions)

@app.route('/submit-assignment/<int:assignment_id>', methods=['POST'])
@login_required
@role_required('student')
def submit_assignment(assignment_id):
    submission_text = request.form.get('submission_text', '')

    # Handle file upload
    file_path = None
    if 'assignment_file' in request.files:
        file = request.files['assignment_file']
        if file and file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(f"{session['user_id']}_{assignment_id}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            file_path = filename

    db = get_db()
    existing = db.execute('SELECT * FROM submissions WHERE assignment_id = ? AND student_id = ?',
                         (assignment_id, session['user_id'])).fetchone()

    if existing:
        db.execute('''
            UPDATE submissions
            SET submission_text = ?, file_path = ?, submitted_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (submission_text, file_path, existing['id']))
        flash('Assignment resubmitted!', 'success')
    else:
        db.execute('''
            INSERT INTO submissions (assignment_id, student_id, submission_text, file_path)
            VALUES (?, ?, ?, ?)
        ''', (assignment_id, session['user_id'], submission_text, file_path))
        flash('Assignment submitted!', 'success')

    db.commit()
    log_activity(session['user_id'], f"Submitted assignment {assignment_id}")

    return redirect(url_for('view_assignment', assignment_id=assignment_id))

@app.route('/grade-submission/<int:submission_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin')
def grade_submission(submission_id):
    grade = int(request.form['grade'])
    feedback = request.form.get('feedback', '')

    # Ensure grade doesn't exceed 100
    if grade > 100:
        grade = 100
        flash('Grade capped at 100 (maximum allowed)', 'warning')

    db = get_db()
    submission = db.execute('SELECT s.*, u.mobile as student_mobile FROM submissions s JOIN users u ON s.student_id = u.id WHERE s.id = ?', (submission_id,)).fetchone()

    if submission:
        db.execute('UPDATE submissions SET grade = ?, graded_at = CURRENT_TIMESTAMP, feedback = ? WHERE id = ?',
                  (grade, feedback, submission_id))
        db.commit()
        log_activity(session['user_id'], f"Graded submission {submission_id}")

        send_sms_alert(submission['student_mobile'], f"Your assignment has been graded: {grade} points")
        flash('Submission graded!', 'success')
    else:
        flash('Submission not found!', 'danger')

    return redirect(request.referrer or url_for('teacher_dashboard'))

# QUIZZES
@app.route('/create-quiz/<int:course_id>', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def create_quiz(course_id):
    if request.method == 'POST':
        title = request.form['title']
        total_points = int(request.form.get('total_points', 100))

        db = get_db()
        cursor = db.execute('INSERT INTO quizzes (course_id, title, total_points) VALUES (?, ?, ?)',
                           (course_id, title, total_points))
        quiz_id = cursor.lastrowid

        question_texts = request.form.getlist('question_text[]')
        correct_answers = request.form.getlist('correct_answer[]')
        points_list = request.form.getlist('points[]')

        for i in range(len(question_texts)):
            if question_texts[i] and correct_answers[i]:
                db.execute('''
                    INSERT INTO quiz_questions (quiz_id, question_text, correct_answer, points)
                    VALUES (?, ?, ?, ?)
                ''', (quiz_id, question_texts[i], correct_answers[i], points_list[i] if i < len(points_list) else 10))

        db.commit()
        log_activity(session['user_id'], f"Created quiz: {title}")

        # Notify all enrolled students
        students = db.execute('''
            SELECT DISTINCT u.id, u.name FROM users u
            JOIN enrollments e ON u.id = e.student_id
            WHERE e.course_id = ?
        ''', (course_id,)).fetchall()

        for student in students:
            create_student_notification(student['id'], f"New Quiz: {title}",
                                       f"A new quiz '{title}' has been posted in your course.",
                                       'quiz', quiz_id)

        flash('Quiz created! Students have been notified.', 'success')
        return redirect(url_for('view_course', course_id=course_id))

    return render_template('create_quiz.html', course_id=course_id)

@app.route('/take-quiz/<int:quiz_id>', methods=['GET', 'POST'])
@login_required
@role_required('student')
def take_quiz(quiz_id):
    db = get_db()
    quiz = db.execute('SELECT q.*, c.title as course_title FROM quizzes q JOIN courses c ON q.course_id = c.id WHERE q.id = ?', (quiz_id,)).fetchone()

    if not quiz:
        flash('Quiz not found!', 'danger')
        return redirect(url_for('dashboard'))

    existing_attempt = db.execute('SELECT * FROM quiz_attempts WHERE quiz_id = ? AND student_id = ?',
                                  (quiz_id, session['user_id'])).fetchone()
    if existing_attempt:
        flash('You have already taken this quiz!', 'warning')
        return redirect(url_for('view_course', course_id=quiz['course_id']))

    questions = db.execute('SELECT * FROM quiz_questions WHERE quiz_id = ?', (quiz_id,)).fetchall()

    if request.method == 'POST':
        score = 0
        for question in questions:
            user_answer = request.form.get(f'q_{question["id"]}', '')
            if user_answer.lower().strip() == question['correct_answer'].lower().strip():
                score += question['points']

        db.execute('INSERT INTO quiz_attempts (quiz_id, student_id, score) VALUES (?, ?, ?)',
                  (quiz_id, session['user_id'], score))
        db.commit()
        log_activity(session['user_id'], f"Completed quiz {quiz_id} with score {score}")

        send_sms_alert(session['mobile'], f"You scored {score}/{quiz['total_points']} on '{quiz['title']}'")
        flash(f'Quiz completed! Score: {score}/{quiz["total_points"]}', 'success')
        return redirect(url_for('student_performance'))

    return render_template('take_quiz.html', quiz=quiz, questions=questions)

# PERFORMANCE
@app.route('/student-performance')
@login_required
@role_required('student')
def student_performance():
    user_id = session['user_id']
    db = get_db()

    assignment_grades = db.execute('''
        SELECT a.title as assignment_title, c.title as course_title, s.grade, a.max_points
        FROM submissions s
        JOIN assignments a ON s.assignment_id = a.id
        JOIN courses c ON a.course_id = c.id
        WHERE s.student_id = ? AND s.grade IS NOT NULL
    ''', (user_id,)).fetchall()

    quiz_scores = db.execute('''
        SELECT q.title as quiz_title, c.title as course_title, qa.score, q.total_points
        FROM quiz_attempts qa
        JOIN quizzes q ON qa.quiz_id = q.id
        JOIN courses c ON q.course_id = c.id
        WHERE qa.student_id = ?
    ''', (user_id,)).fetchall()

    attendance_data = db.execute('''
        SELECT c.id as course_id, c.title as course_title,
               SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) as present_days,
               COUNT(*) as total_days
        FROM attendance a
        JOIN courses c ON a.course_id = c.id
        WHERE a.student_id = ?
        GROUP BY c.id
    ''', (user_id,)).fetchall()

    for record in attendance_data:
        record['percentage'] = (record['present_days'] / record['total_days'] * 100) if record['total_days'] > 0 else 0

    return render_template('student_performance.html',
                         assignment_grades=assignment_grades,
                         quiz_scores=quiz_scores,
                         attendance_data=attendance_data)

# ATTENDANCE SHEET
@app.route('/attendance-sheet/<int:course_id>')
@login_required
@role_required('teacher', 'admin')
def attendance_sheet(course_id):
    db = get_db()
    course = db.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()

    students = db.execute('''
        SELECT u.id, u.name FROM users u
        JOIN enrollments e ON u.id = e.student_id
        WHERE e.course_id = ? ORDER BY u.name
    ''', (course_id,)).fetchall()

    dates = db.execute('''
        SELECT DISTINCT date FROM attendance WHERE course_id = ? ORDER BY date DESC
    ''', (course_id,)).fetchall()

    attendance_data = {}
    for student in students:
        attendance_data[student['id']] = {}
        records = db.execute('SELECT date, status FROM attendance WHERE student_id = ? AND course_id = ?',
                            (student['id'], course_id)).fetchall()
        for rec in records:
            attendance_data[student['id']][rec['date']] = rec['status']

    return render_template('attendance_sheet.html', course=course, students=students, dates=dates, attendance_data=attendance_data)

@app.route('/mark-attendance/<int:course_id>', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def mark_attendance(course_id):
    db = get_db()
    course = db.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()

    students = db.execute('''
        SELECT u.id, u.name
        FROM users u
        JOIN enrollments e ON u.id = e.student_id
        WHERE e.course_id = ?
    ''', (course_id,)).fetchall()

    if request.method == 'POST':
        date = request.form['date']
        for student in students:
            status = request.form.get(f'attendance_{student["id"]}')
            if status:
                existing = db.execute('SELECT * FROM attendance WHERE student_id = ? AND course_id = ? AND date = ?',
                                     (student['id'], course_id, date)).fetchone()
                if existing:
                    db.execute('UPDATE attendance SET status = ? WHERE id = ?', (status, existing['id']))
                else:
                    db.execute('INSERT INTO attendance (student_id, course_id, date, status) VALUES (?, ?, ?, ?)',
                              (student['id'], course_id, date, status))
        db.commit()
        log_activity(session['user_id'], f"Marked attendance for course {course_id} on {date}")
        flash('Attendance marked successfully!', 'success')
        return redirect(url_for('attendance_sheet', course_id=course_id))

    return render_template('mark_attendance.html', course=course, students=students)

# ANNOUNCEMENTS
@app.route('/create-announcement', methods=['GET', 'POST'])
@login_required
@role_required('teacher', 'admin')
def create_announcement():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        audience = request.form.get('audience', 'teachers')

        db = get_db()
        db.execute('INSERT INTO announcements (title, content, posted_by, audience) VALUES (?, ?, ?, ?)',
                  (title, content, session['user_id'], audience))
        db.commit()
        log_activity(session['user_id'], f"Posted announcement: {title}")

        if audience == 'teachers':
            teachers = db.execute('SELECT mobile FROM users WHERE role = "teacher"').fetchall()
            for teacher in teachers:
                send_sms_alert(teacher['mobile'], f"New Announcement: {title}")
        else:
            students = db.execute('SELECT mobile FROM users WHERE role = "student"').fetchall()
            for student in students:
                send_sms_alert(student['mobile'], f"New Announcement: {title}")

        flash('Announcement posted successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('create_announcement.html')

# DISCUSSIONS
@app.route('/add-discussion/<int:course_id>', methods=['POST'])
@login_required
def add_discussion(course_id):
    message = request.form['message']
    parent_id = request.form.get('parent_id')

    db = get_db()
    db.execute('INSERT INTO discussions (course_id, user_id, message, parent_id) VALUES (?, ?, ?, ?)',
              (course_id, session['user_id'], message, parent_id))
    db.commit()

    log_activity(session['user_id'], f"Posted discussion in course {course_id}")
    flash('Message posted!', 'success')
    return redirect(url_for('view_course', course_id=course_id))

# SYSTEM REPORTS
@app.route('/system-reports')
@login_required
@role_required('admin')
def system_reports():
    db = get_db()

    activities = db.execute('''
        SELECT al.*, u.name as user_name, u.role as user_role
        FROM activity_logs al
        JOIN users u ON al.user_id = u.id
        ORDER BY al.timestamp DESC LIMIT 100
    ''').fetchall()

    course_stats = db.execute('''
        SELECT c.title, COUNT(e.id) as enrollment_count, COUNT(DISTINCT a.id) as assignment_count
        FROM courses c
        LEFT JOIN enrollments e ON c.id = e.course_id
        LEFT JOIN assignments a ON c.id = a.course_id
        GROUP BY c.id
    ''').fetchall()

    return render_template('system_reports.html', activities=activities, course_stats=course_stats)

# Download uploaded file
@app.route('/download/<filename>')
@login_required
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ==================== RUN APP ====================
if __name__ == '__main__':
    with app.app_context():
        init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)