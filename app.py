
import os
import math
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text


def local_now():
    """Return current Philippines time as a timezone-naive datetime."""
    return datetime.now(ZoneInfo('Asia/Manila')).replace(tzinfo=None)


# ==========================================
# APP CONFIGURATION
# ==========================================

app = Flask(__name__)

app.config['SECRET_KEY'] = os.environ.get(
    'SECRET_KEY',
    'change-this-before-deployment'
)

database_url = os.environ.get(
    'DATABASE_URL',
    'sqlite:///attendance.db'
)

if database_url.startswith('postgres://'):
    database_url = database_url.replace(
        'postgres://',
        'postgresql://',
        1
    )

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ==========================================
# DATABASE FIXES
# ==========================================

with app.app_context():
    fixes = [
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS section VARCHAR(100)',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS schedule_id INTEGER',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS attendance_date DATE',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS check_in_time TIMESTAMP',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS check_out_time TIMESTAMP',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS status VARCHAR(50)',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS last_inside_at TIMESTAMP',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS last_location_at TIMESTAMP',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS location_status VARCHAR(30)',
        'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS departure_alerted BOOLEAN DEFAULT FALSE',

        # FIX: Remove the old rule allowing only one attendance record per day.
        'ALTER TABLE attendance DROP CONSTRAINT IF EXISTS one_checkin_per_day',

        # Absent students do not have a check-in timestamp.
        'ALTER TABLE attendance ALTER COLUMN check_in_time DROP NOT NULL'
    ]

    for sql in fixes:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Fix skipped: {sql} -> {e}")


TEACHER_CODE = os.environ.get(
    'TEACHER_CODE',
    'TEACHER2026'
)

CAMPUS_LATITUDE = float(
    os.environ.get('CAMPUS_LATITUDE', '14.148458')
)

CAMPUS_LONGITUDE = float(
    os.environ.get('CAMPUS_LONGITUDE', '121.311829')
)

CAMPUS_RADIUS_METERS = float(
    os.environ.get('CAMPUS_RADIUS_METERS', '10000')
)

LATE_GRACE_MINUTES = 15


# ==========================================
# GPS DISTANCE CALCULATION
# ==========================================

def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return r * 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(max(0, 1 - a))
    )


# ==========================================
# DATABASE MODELS
# ==========================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    username = db.Column(
        db.String(50),
        unique=True,
        nullable=False
    )

    password = db.Column(
        db.String(200),
        nullable=False
    )

    role = db.Column(
        db.String(20),
        nullable=False
    )

    section = db.Column(
        db.String(80),
        default=''
    )


class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False
    )

    subject = db.Column(
        db.String(120),
        nullable=False
    )

    section = db.Column(
        db.String(80),
        nullable=False
    )

    room = db.Column(
        db.String(80),
        default=''
    )

    # Monday = 0, Sunday = 6
    day_of_week = db.Column(
        db.Integer,
        nullable=False
    )

    start_time = db.Column(
        db.Time,
        nullable=False
    )

    end_time = db.Column(
        db.Time,
        nullable=False
    )

    grace_minutes = db.Column(
        db.Integer,
        default=10
    )

    teacher = db.relationship(
        'User',
        backref='schedules'
    )


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False
    )

    schedule_id = db.Column(
        db.Integer,
        db.ForeignKey('schedule.id'),
        nullable=False
    )

    attendance_date = db.Column(
        db.Date,
        nullable=False,
        default=date.today
    )

    check_in_time = db.Column(db.DateTime)

    check_out_time = db.Column(db.DateTime)

    status = db.Column(
        db.String(30),
        default='Absent'
    )

    last_inside_at = db.Column(db.DateTime)

    last_location_at = db.Column(db.DateTime)

    location_status = db.Column(
        db.String(30),
        default='Location Lost'
    )

    departure_alerted = db.Column(
        db.Boolean,
        default=False
    )

    student = db.relationship(
        'User',
        backref='attendance_records'
    )

    schedule = db.relationship(
        'Schedule',
        backref='attendance_records'
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'schedule_id',
            'attendance_date',
            name='one_record_per_student_class_day'
        ),
    )


class Notice(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False
    )

    student_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False
    )

    schedule_id = db.Column(
        db.Integer,
        db.ForeignKey('schedule.id'),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.now,
        nullable=False
    )

    message = db.Column(
        db.String(255),
        nullable=False
    )

    read = db.Column(
        db.Boolean,
        default=False
    )

    student = db.relationship(
        'User',
        foreign_keys=[student_id]
    )

    schedule = db.relationship('Schedule')


# Create missing database tables.
# This does not migrate existing tables.
with app.app_context():
    db.create_all()


# ==========================================
# LOGIN AND ROLE PROTECTION
# ==========================================

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))

        return fn(*args, **kwargs)

    return wrapper


def role_required(role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))

            if session.get('role') != role:
                flash(
                    'You do not have permission to open that page.',
                    'error'
                )

                return redirect(url_for('dashboard'))

            return fn(*args, **kwargs)

        return wrapper

    return deco


# ==========================================
# ATTENDANCE HELPERS
# ==========================================

def active_classes_for_student(student, now=None):
    now = now or local_now()

    return Schedule.query.filter_by(
        section=student.section,
        day_of_week=now.weekday()
    ).all()


def class_is_active(schedule, now=None):
    now = now or local_now()

    start = datetime.combine(
        now.date(),
        schedule.start_time
    )

    end = datetime.combine(
        now.date(),
        schedule.end_time
    )

    return start <= now <= end


LOCATION_LOST_TIMEOUT_SECONDS = 60


def get_location_status(record, now=None):
    """Return the live location status without changing attendance status."""
    now = now or local_now()

    if record.check_out_time:
        return 'Checked Out'

    if not record.check_in_time:
        return 'Not Checked In'

    if (
        not record.last_location_at
        or (now - record.last_location_at).total_seconds()
        > LOCATION_LOST_TIMEOUT_SECONDS
    ):
        return 'Location Lost'

    if record.location_status in ('On Campus', 'Outside Campus'):
        return record.location_status

    return 'Location Lost'


def get_or_create_record(student, schedule, day=None):
    day = day or local_now().date()

    record = Attendance.query.filter_by(
        user_id=student.id,
        schedule_id=schedule.id,
        attendance_date=day
    ).first()

    if record is None:
        record = Attendance(
            user_id=student.id,
            schedule_id=schedule.id,
            attendance_date=day,
            check_in_time=None,
            check_out_time=None,
            status='Absent',
            last_inside_at=None,
            departure_alerted=False
        )

        db.session.add(record)
        db.session.flush()

    return record


# ==========================================
# HOME PAGE
# ==========================================

@app.route('/')
def home():
    return render_template('home.html')


# ==========================================
# REGISTER
# ==========================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'student')
        section = request.form.get('section', '').strip()
        code = request.form.get('teacher_code', '')

        message = None

        if not username or not password:
            message = 'Username and password are required.'

        elif len(password) < 8:
            message = 'Password must be at least 8 characters.'

        elif role not in ('student', 'teacher'):
            message = 'Invalid account type.'

        elif role == 'teacher' and code != TEACHER_CODE:
            message = 'Invalid teacher authorization code.'

        elif role == 'student' and not section:
            message = (
                'Enter your class/section so schedules can be matched.'
            )

        elif User.query.filter_by(username=username).first():
            message = 'Username already exists.'

        if message:
            return render_template(
                'register.html',
                message=message
            )

        user = User(
            username=username,
            password=generate_password_hash(password),
            role=role,
            section=section if role == 'student' else ''
        )

        db.session.add(user)
        db.session.commit()

        flash(
            'Account created. You can now sign in.',
            'success'
        )

        return redirect(url_for('login'))

    return render_template(
        'register.html',
        message=''
    )


# ==========================================
# LOGIN
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(
            username=username
        ).first()

        if user and check_password_hash(user.password, password):
            session.clear()

            session.update(
                user_id=user.id,
                username=user.username,
                role=user.role
            )

            return redirect(url_for('dashboard'))

        return render_template(
            'login.html',
            message='Incorrect username or password.'
        )

    return render_template(
        'login.html',
        message=''
    )


# ==========================================
# LOGOUT
# ==========================================

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ==========================================
# GENERAL DASHBOARD
# ==========================================

@app.route('/dashboard')
@login_required
def dashboard():
    if session['role'] == 'teacher':
        return redirect(url_for('teacher_dashboard'))

    student = db.session.get(User, session['user_id'])

    schedules = sorted(
        active_classes_for_student(student),
        key=lambda s: s.start_time
    )

    records = Attendance.query.filter_by(
        user_id=student.id
    ).order_by(
        Attendance.attendance_date.desc()
    ).limit(30).all()

    now = local_now()
    class_sessions = []

    for schedule in schedules:
        record = Attendance.query.filter_by(
            user_id=student.id,
            schedule_id=schedule.id,
            attendance_date=now.date()
        ).first()

        check_in_start = (
            datetime.combine(now.date(), schedule.start_time)
            - timedelta(minutes=10)
        )
        check_in_end = datetime.combine(now.date(), schedule.end_time)

        class_sessions.append({
            'schedule': schedule,
            'record': record,
            'can_check_in': (
                check_in_start <= now <= check_in_end
                and (record is None or record.check_in_time is None)
            ),
            'can_check_out': (
                record is not None
                and record.check_in_time is not None
                and record.check_out_time is None
            ),
            'is_absent': (
                (record is None or record.check_in_time is None)
                and now > (
                    datetime.combine(now.date(), schedule.start_time)
                    + timedelta(minutes=(
                        schedule.grace_minutes
                        if schedule.grace_minutes is not None
                        else LATE_GRACE_MINUTES
                    ))
                )
            )
        })

    return render_template(
        'student_dashboard.html',
        student=student,
        schedules=schedules,
        class_sessions=class_sessions,
        records=records,
        now=now,
        tracking_active=any(
            record.check_in_time and not record.check_out_time
            and record.attendance_date == now.date()
            for record in records
        )
    )


# ==========================================
# TEACHER DASHBOARD
# ==========================================

@app.route('/teacher')
@login_required
@role_required('teacher')
def teacher_dashboard():
    teacher_id = session['user_id']

    schedules = Schedule.query.filter_by(
        teacher_id=teacher_id
    ).order_by(
        Schedule.day_of_week,
        Schedule.start_time
    ).all()

    today_schedules = [
        s for s in schedules
        if s.day_of_week == local_now().date().weekday()
    ]

    now = local_now()

    for sch in today_schedules:
        students = User.query.filter_by(
            role='student',
            section=sch.section
        ).all()

        for student in students:
            record = get_or_create_record(student, sch)

            absent_after = (
                datetime.combine(local_now().date(), sch.start_time)
                + timedelta(
                    minutes=(
                        sch.grace_minutes
                        if sch.grace_minutes is not None
                        else LATE_GRACE_MINUTES
                    )
                )
            )

            if record.check_in_time is None and now > absent_after:
                record.status = 'Absent'

    db.session.commit()

    location_rows = []

    for sch in today_schedules:
        students = User.query.filter_by(
            role='student',
            section=sch.section
        ).order_by(User.username).all()

        for student in students:
            record = Attendance.query.filter_by(
                user_id=student.id,
                schedule_id=sch.id,
                attendance_date=now.date()
            ).first()

            if record and record.check_in_time:
                location_rows.append({
                    'student': student,
                    'schedule': sch,
                    'record': record,
                    'status': get_location_status(record, now),
                    'last_update': record.last_location_at
                })

    notices = Notice.query.filter_by(
        teacher_id=teacher_id
    ).order_by(
        Notice.created_at.desc()
    ).limit(30).all()

    stats = {
        'present': 0,
        'late': 0,
        'absent': 0,
        'alerts': Notice.query.filter_by(
            teacher_id=teacher_id,
            read=False
        ).count()
    }

    for sch in today_schedules:
        records = Attendance.query.filter_by(
            schedule_id=sch.id,
            attendance_date=local_now().date()
        ).all()

        for record in records:
            status = record.status.lower()

            if status in stats:
                stats[status] += 1

    return render_template(
        'teacher_dashboard.html',
        schedules=schedules,
        today_schedules=today_schedules,
        notices=notices,
        location_rows=location_rows,
        stats=stats,
        day_names=[
            'Monday',
            'Tuesday',
            'Wednesday',
            'Thursday',
            'Friday',
            'Saturday',
            'Sunday'
        ]
    )


# ==========================================
# ADD CLASS SCHEDULE
# ==========================================

@app.route('/schedule/add', methods=['POST'])
@login_required
@role_required('teacher')
def add_schedule():
    try:
        subject = request.form.get('subject', '').strip()
        section = request.form.get('section', '').strip()
        room = request.form.get('room', '').strip()

        day_value = request.form.get('day_of_week', '')
        day = int(day_value)

        start_value = request.form.get('start_time', '')
        end_value = request.form.get('end_time', '')

        start = time.fromisoformat(start_value)
        end = time.fromisoformat(end_value)

        grace = 10

        if not subject:
            raise ValueError('Please enter a subject.')

        if not section:
            raise ValueError('Please enter a section.')

        if day not in range(7):
            raise ValueError('Please select a valid day.')

        if start >= end:
            raise ValueError(
                'End time must be later than start time.'
            )

        # Prevent section and teacher schedule conflicts.
        day_schedules = Schedule.query.filter_by(
            day_of_week=day
        ).all()

        section_conflicts = [
            item for item in day_schedules
            if item.section.strip().casefold() == section.casefold()
            and start < item.end_time
            and end > item.start_time
        ]

        if section_conflicts:
            conflict = section_conflicts[0]
            raise ValueError(
                f"Section {section} already has {conflict.subject} "
                f"from {conflict.start_time.strftime('%I:%M %p')} to "
                f"{conflict.end_time.strftime('%I:%M %p')} on that day."
            )

        teacher_conflicts = [
            item for item in day_schedules
            if item.teacher_id == session['user_id']
            and start < item.end_time
            and end > item.start_time
        ]

        if teacher_conflicts:
            conflict = teacher_conflicts[0]
            raise ValueError(
                f"You already have {conflict.subject} from "
                f"{conflict.start_time.strftime('%I:%M %p')} to "
                f"{conflict.end_time.strftime('%I:%M %p')} on that day."
            )

    except (ValueError, TypeError) as error:
        message = str(error)

        if not message or message == 'invalid literal for int() with base 10:':
            message = 'Please check the day and time fields.'

        flash(
            'Unable to add schedule. ' + message,
            'error'
        )

        return redirect(url_for('teacher_dashboard'))

    schedule = Schedule(
        teacher_id=session['user_id'],
        subject=subject,
        section=section,
        room=room,
        day_of_week=day,
        start_time=start,
        end_time=end,
        grace_minutes=grace
    )

    try:
        db.session.add(schedule)
        db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception('Failed to save class schedule')

        flash(
            'The schedule could not be saved. Please try again.',
            'error'
        )

        return redirect(url_for('teacher_dashboard'))

    flash('Class schedule added successfully!', 'success')
    return redirect(url_for('teacher_dashboard'))


@app.route('/schedule/section-preview')
@login_required
@role_required('teacher')
def section_schedule_preview():
    section = request.args.get('section', '').strip()
    day_value = request.args.get('day', '')

    try:
        day = int(day_value)
        if day not in range(7):
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(schedules=[])

    if not section:
        return jsonify(schedules=[])

    schedules = Schedule.query.filter_by(day_of_week=day).all()
    schedules = [
        item for item in schedules
        if item.section.strip().casefold() == section.casefold()
    ]
    schedules.sort(key=lambda item: item.start_time)

    return jsonify(schedules=[
        {
            'subject': item.subject,
            'teacher': item.teacher.username,
            'room': item.room or '',
            'start': item.start_time.strftime('%I:%M %p'),
            'end': item.end_time.strftime('%I:%M %p')
        }
        for item in schedules
    ])


# ==========================================
# DELETE CLASS SCHEDULE
# ==========================================

@app.route(
    '/schedule/<int:schedule_id>/delete',
    methods=['POST']
)
@login_required
@role_required('teacher')
def delete_schedule(schedule_id):
    schedule = Schedule.query.filter_by(
        id=schedule_id,
        teacher_id=session['user_id']
    ).first_or_404()

    existing_record = Attendance.query.filter_by(
        schedule_id=schedule.id
    ).first()

    if existing_record:
        flash(
            'This schedule already has attendance records, '
            'so it cannot be deleted.',
            'error'
        )
    else:
        db.session.delete(schedule)
        db.session.commit()
        flash('Schedule deleted.', 'success')

    return redirect(url_for('teacher_dashboard'))


# ==========================================
# TEACHER ATTENDANCE RECORDS
# ==========================================

@app.route('/teacher/attendance')
@login_required
@role_required('teacher')
def teacher_attendance():
    try:
        selected_date = date.fromisoformat(
            request.args.get('date', local_now().date().isoformat())
        )
    except ValueError:
        selected_date = local_now().date()

    schedules = Schedule.query.filter_by(
        teacher_id=session['user_id']
    ).all()

    rows = []

    for schedule in schedules:
        students = User.query.filter_by(
            role='student',
            section=schedule.section
        ).order_by(User.username).all()

        for student in students:
            record = Attendance.query.filter_by(
                user_id=student.id,
                schedule_id=schedule.id,
                attendance_date=selected_date
            ).first()

            status = record.status if record else 'Absent'

            rows.append({
                'student': student,
                'schedule': schedule,
                'record': record,
                'status': status
            })

    return render_template(
        'teacher_attendance.html',
        rows=rows,
        selected_date=selected_date
    )


# ==========================================
# STUDENT CHECK-IN
# ==========================================

@app.route('/check-in', methods=['POST'])
@login_required
@role_required('student')
def check_in():
    student = db.session.get(User, session['user_id'])
    now = local_now()

    def reply(message, ok=False, status=400):
        return jsonify(ok=ok, message=message), status

    try:
        lat = float(request.form.get('latitude', ''))
        lon = float(request.form.get('longitude', ''))
        accuracy = float(request.form.get('accuracy', ''))

        if (
            not all(math.isfinite(v) for v in (lat, lon, accuracy))
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or not 0 < accuracy <= 50
        ):
            return reply(
                'GPS accuracy must be 50 meters or better. Try again.',
                status=400
            )

    except (ValueError, TypeError):
        return reply(
            'GPS data is missing. Allow location access and try again.',
            status=400
        )

    distance = distance_m(
        CAMPUS_LATITUDE,
        CAMPUS_LONGITUDE,
        lat,
        lon
    )

    if distance > CAMPUS_RADIUS_METERS:
        return reply(
            'You appear to be outside the campus attendance area.',
            status=403
        )

    schedule_id = request.form.get('schedule_id', type=int)
    if not schedule_id:
        return reply(
            'Choose the specific class you want to check in for.',
            status=400
        )

    schedule = Schedule.query.filter_by(
        id=schedule_id,
        section=student.section,
        day_of_week=now.weekday()
    ).first()

    if schedule is None:
        return reply(
            'That class is not scheduled for your section today.',
            status=404
        )

    check_in_start = (
        datetime.combine(now.date(), schedule.start_time)
        - timedelta(minutes=10)
    )
    check_in_end = datetime.combine(now.date(), schedule.end_time)

    if not check_in_start <= now <= check_in_end:
        return reply(
            'This class is outside its check-in window. Check the class schedule.',
            status=400
        )

    try:
        record = get_or_create_record(student, schedule, now.date())

        if record.check_in_time is not None:
            return reply(
                'You are already checked in for this class.',
                status=409
            )

        record.check_in_time = now
        record.last_inside_at = now
        record.last_location_at = now
        record.location_status = 'On Campus'
        record.departure_alerted = False

        grace = (
            schedule.grace_minutes
            if schedule.grace_minutes is not None
            else LATE_GRACE_MINUTES
        )
        late_after = (
            datetime.combine(now.date(), schedule.start_time)
            + timedelta(minutes=grace)
        )
        record.status = 'Late' if now > late_after else 'Present'

        db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception('Failed to save student check-in')
        return reply(
            'The server could not save your attendance. Please try again.',
            status=500
        )

    status_message = 'Late' if record.status == 'Late' else 'Present (on time)'
    return reply(
        f'{schedule.subject}: check-in saved. Attendance: {status_message}.',
        ok=True,
        status=200
    )


# ==========================================
# STUDENT CHECK-OUT
# ==========================================

@app.route('/check-out', methods=['POST'])
@login_required
@role_required('student')
def check_out():
    student = db.session.get(User, session['user_id'])
    now = local_now()
    schedule_id = request.form.get('schedule_id', type=int)

    if not schedule_id:
        return jsonify(
            ok=False,
            message='Choose the specific class you want to check out from.'
        ), 400

    schedule = Schedule.query.filter_by(
        id=schedule_id,
        section=student.section,
        day_of_week=now.weekday()
    ).first()

    if schedule is None:
        return jsonify(
            ok=False,
            message='That class is not scheduled for your section today.'
        ), 404

    record = Attendance.query.filter_by(
        user_id=student.id,
        schedule_id=schedule.id,
        attendance_date=now.date()
    ).first()

    if not record or not record.check_in_time:
        return jsonify(
            ok=False,
            message='You have not checked in for this class.'
        ), 400

    if record.check_out_time:
        return jsonify(
            ok=False,
            message='You have already checked out of this class.'
        ), 409

    try:
        record.check_out_time = now
        record.location_status = 'Checked Out'
        db.session.commit()

    except Exception:
        db.session.rollback()
        app.logger.exception('Failed to save student check-out')
        return jsonify(
            ok=False,
            message='The server could not save your check-out.'
        ), 500

    return jsonify(
        ok=True,
        message=f'{schedule.subject}: check-out recorded.'
    ), 200


# ==========================================
# STUDENT LOCATION UPDATES
# ==========================================

@app.route('/location-update', methods=['POST'])
@login_required
@role_required('student')
def location_update():
    student = db.session.get(User, session['user_id'])
    now = local_now()

    try:
        lat = float(request.form['latitude'])
        lon = float(request.form['longitude'])
        accuracy = float(request.form['accuracy'])

        if (
            not all(math.isfinite(v) for v in (lat, lon, accuracy))
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or accuracy <= 0
            or accuracy > 100
        ):
            raise ValueError()

    except (ValueError, KeyError):
        return jsonify(
            ok=False,
            message='Invalid GPS reading'
        ), 400

    inside = (
        distance_m(
            CAMPUS_LATITUDE,
            CAMPUS_LONGITUDE,
            lat,
            lon
        ) <= CAMPUS_RADIUS_METERS
    )

    alerts = 0

    for schedule in active_classes_for_student(student, now):
        class_end = datetime.combine(now.date(), schedule.end_time)
        if now > class_end:
            continue

        record = Attendance.query.filter_by(
            user_id=student.id,
            schedule_id=schedule.id,
            attendance_date=now.date()
        ).first()

        if (
            not record
            or not record.check_in_time
            or record.check_out_time
        ):
            continue

        record.last_location_at = now

        if inside:
            record.last_inside_at = now
            record.location_status = 'On Campus'
            record.departure_alerted = False

        else:
            record.location_status = 'Outside Campus'

        if not inside and not record.departure_alerted:
            message = (
                f'{student.username} appears outside campus '
                f'during {schedule.subject} ({schedule.section}) '
                f'at {now.strftime("%I:%M %p")}.'
            )

            notice = Notice(
                teacher_id=schedule.teacher_id,
                student_id=student.id,
                schedule_id=schedule.id,
                message=message
            )

            db.session.add(notice)
            record.departure_alerted = True
            alerts += 1

    db.session.commit()

    return jsonify(
        ok=True,
        inside=inside,
        location_status='On Campus' if inside else 'Outside Campus',
        alerts_created=alerts
    )


# ==========================================
# TEACHER LIVE LOCATION STATUS
# ==========================================

@app.route('/teacher/location-status')
@login_required
@role_required('teacher')
def teacher_location_status():
    now = local_now()

    records = Attendance.query.join(
        Schedule,
        Attendance.schedule_id == Schedule.id
    ).filter(
        Schedule.teacher_id == session['user_id'],
        Attendance.attendance_date == now.date(),
        Attendance.check_in_time.isnot(None)
    ).all()

    rows = []

    for record in records:
        rows.append({
            'record_id': record.id,
            'student': record.student.username,
            'section': record.schedule.section,
            'subject': record.schedule.subject,
            'status': get_location_status(record, now),
            'last_update': (
                record.last_location_at.strftime('%I:%M:%S %p')
                if record.last_location_at else None
            )
        })

    return jsonify(rows=rows)


# ==========================================
# MARK TEACHER NOTICE AS READ
# ==========================================

@app.route(
    '/teacher/notices/<int:notice_id>/read',
    methods=['POST']
)
@login_required
@role_required('teacher')
def read_notice(notice_id):
    notice = Notice.query.filter_by(
        id=notice_id,
        teacher_id=session['user_id']
    ).first_or_404()

    notice.read = True
    db.session.commit()

    return redirect(url_for('teacher_dashboard'))


# ==========================================
# GPS TEST
# ==========================================

@app.route('/gps-test')
def gps_test():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    return render_template('gps_test.html')


# ==========================================
# RUN THE APPLICATION
# ==========================================

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('FLASK_DEBUG') == '1'
    )
