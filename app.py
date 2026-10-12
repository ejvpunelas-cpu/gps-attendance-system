
import os
import math
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# Use a secret stored in Render's environment settings.
# The fallback is only for local development.
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "local-development-key-change-this"
)

# Use PostgreSQL online, or SQLite when running locally.
database_url = os.environ.get("DATABASE_URL", "sqlite:///attendance.db")

# Support connection URLs that start with postgres://
if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://", "postgresql://", 1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Set these in Render instead of keeping the teacher code in public code.
TEACHER_CODE = os.environ.get("TEACHER_CODE", "TEACHER2026")

CAMPUS_LATITUDE = 14.148458
CAMPUS_LONGITUDE = 121.311829
CAMPUS_RADIUS_METERS = 100
MAX_GPS_ACCURACY_METERS = 50


# ==========================================
# DISTANCE CALCULATION
# ==========================================

def calculate_distance(lat1, lon1, lat2, lon2):
    earth_radius = 6371000

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    difference_lat = lat2 - lat1
    difference_lon = lon2 - lon1

    a = (
        math.sin(difference_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(difference_lon / 2) ** 2
    )

    # Protect against tiny floating-point rounding errors
    a = max(0, min(1, a))

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius * c

# ==========================================
# DATABASE MODELS
# ==========================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    attendance_date = db.Column(db.Date, nullable=False)
    check_in_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(30), default="Checked In")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "attendance_date",
            name="one_checkin_per_day"
        ),
    )


with app.app_context():
    db.create_all()

# ==========================================
# HOME
# ==========================================

@app.route("/")
def home():
    return """
    <h1>GPS Attendance System</h1>
    <p>Student Attendance Monitoring System</p>
    <a href="/register">Create Account</a><br><br>
    <a href="/login">Log In</a>
    """

# ==========================================
# REGISTER
# ==========================================

@app.route("/register", methods=["GET", "POST"])
def register():
    message = ""

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        role = request.form["role"]
        teacher_code = request.form.get("teacher_code", "")

        if not username or not password:
            message = "Username and password are required."

        elif len(password) < 8:
            message = "Password must be at least 8 characters."

        elif role not in ["student", "teacher"]:
            message = "Invalid account type."

        elif role == "teacher" and teacher_code != TEACHER_CODE:
            message = "Invalid teacher authorization code."

        elif User.query.filter_by(username=username).first():
            message = "Username already exists."

        else:
            new_user = User(
                username=username,
                password=generate_password_hash(password),
                role=role
            )

            db.session.add(new_user)
            db.session.commit()

            return redirect(url_for("login"))

    return render_template("register.html", message=message)

# ==========================================
# LOGIN
# ==========================================


# ==========================================
# LOGIN
# ==========================================

@app.route("/login", methods=["GET", "POST"])
def login():
    message = ""

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role

            return redirect(url_for("dashboard"))

        message = "Incorrect username or password."

    return render_template("login.html", message=message)

# ==========================================
# DASHBOARD
# ==========================================

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    today = datetime.now().date()

    attendance = Attendance.query.filter_by(
        user_id=session["user_id"],
        attendance_date=today
    ).first()

    message = session.pop("gps_message", "")

    return render_template(
        "dashboard.html",
        username=session["username"],
        role=session["role"],
        attendance=attendance,
        message=message
    )

# ==========================================
# GPS CHECK-IN
# ==========================================

@app.route("/check-in", methods=["POST"])
def check_in():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        return redirect(url_for("dashboard"))

    # Read GPS values sent by the dashboard form
    try:
        latitude = float(request.form.get("latitude", ""))
        longitude = float(request.form.get("longitude", ""))
        accuracy = float(request.form.get("accuracy", ""))
    except (TypeError, ValueError):
        session["gps_message"] = (
            "GPS data is missing. Please use the GPS check-in button."
        )
        return redirect(url_for("dashboard"))

    # Validate the submitted numbers
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and math.isfinite(accuracy)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
        and 0 < accuracy <= 10000
    ):
        session["gps_message"] = (
            "The GPS reading is invalid. Please try again."
        )
        return redirect(url_for("dashboard"))

    # Require sufficiently accurate GPS
    if accuracy > MAX_GPS_ACCURACY_METERS:
        session["gps_message"] = (
            "Your GPS accuracy is too low. "
            "Please try again with a clearer signal."
        )
        return redirect(url_for("dashboard"))

    # Calculate distance from the provisional campus center
    distance = calculate_distance(
        CAMPUS_LATITUDE,
        CAMPUS_LONGITUDE,
        latitude,
        longitude
    )

    # Check whether the reported location is within the radius
    if distance > CAMPUS_RADIUS_METERS:
        session["gps_message"] = (
            "Your reported location appears outside the campus boundary. "
            "Please check your location and try again."
        )
        return redirect(url_for("dashboard"))

    # Prevent duplicate attendance for the same day
    today = datetime.now().date()

    existing = Attendance.query.filter_by(
        user_id=session["user_id"],
        attendance_date=today
    ).first()

    if existing:
        session["gps_message"] = (
            "You already have an attendance record for today."
        )
        return redirect(url_for("dashboard"))

    # Save attendance only after the GPS checks pass
    record = Attendance(
        user_id=session["user_id"],
        attendance_date=today,
        check_in_time=datetime.now(),
        status="GPS Verified"
    )

    try:
        db.session.add(record)
        db.session.commit()
    except Exception:
        db.session.rollback()
        session["gps_message"] = (
            "Could not save attendance. Please try again."
        )
        return redirect(url_for("dashboard"))

    session["gps_message"] = (
        "GPS check-in saved. Your reported location passed the checks."
    )

    return redirect(url_for("dashboard"))

# ==========================================
# GPS TEST PAGE
# ==========================================

@app.route("/gps-test")
def gps_test():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("gps_test.html")

# ==========================================
# LOGOUT
# ==========================================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ==========================================
# RUN APP
# ==========================================


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1"
    )
