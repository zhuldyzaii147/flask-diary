import os
import sqlite3
from datetime import date, datetime
from functools import lru_cache, wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "urok-4-dnevnik-secret-key"
app.config["SESSION_COOKIE_NAME"] = "dnevnik_student"

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dnevnik.db")

DATE_FORMAT = "%d.%m.%Y"

SUBJECTS = ["Математика", "Русский язык", "Английский язык", "Физика", "История"]


# ---------- База данных ----------

def get_db():
    if "db" not in g:
        if not os.path.exists(DATABASE):
            init_db()
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            value INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            deadline TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    if db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0:
        db.executemany("INSERT INTO subjects (name) VALUES (?)", [(s,) for s in SUBJECTS])
    db.commit()
    db.close()


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    user = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND username = ?",
        (user_id, session.get("username")),
    ).fetchone()
    if user is None:
        session.pop("user_id", None)
        session.pop("username", None)
    return user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            flash("Сначала войдите в аккаунт")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_user():
    return {"user": current_user()}


# ---------- Даты и оценки ----------

def parse_date(text):
    return datetime.strptime(text, DATE_FORMAT).date()


@app.template_filter("ru_date")
def ru_date(text):
    return parse_date(text).strftime("%d.%m.%Y")



def subject_average(subject_id):
    row = get_db().execute(
        "SELECT AVG(value) FROM grades WHERE user_id = ? AND subject_id = ?",
        (session["user_id"], subject_id),
    ).fetchone()
    return row[0]


def final_grade(average):
    if average is None:
        return None
    return int(average + 0.5)


# ---------- Регистрация, вход, выход ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        password2 = request.form["password2"]

        if not username or not password:
            flash("Заполните все поля")
        elif len(password) < 4:
            flash("Пароль должен быть не короче 4 символов")
        elif password != password2:
            flash("Пароли не совпадают")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password) VALUES (?, ?)",
                    (username, generate_password_hash(password, method="pbkdf2:sha256")),
                )
                db.commit()
            except sqlite3.IntegrityError:
                flash("Такой пользователь уже существует")
            else:
                flash("Регистрация прошла успешно! Теперь войдите.")
                return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if user is None or not check_password_hash(user["password"], password):
            flash("Неверный логин или пароль")
        else:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash(f"Добро пожаловать, {user['username']}!")
            return redirect(url_for("diary"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта")
    return redirect(url_for("login"))


# ---------- Дневник ----------

@app.route("/")
@login_required
def diary():
    db = get_db()
    rows = []
    for subject in db.execute("SELECT * FROM subjects ORDER BY id").fetchall():
        grades = [
            r["value"]
            for r in db.execute(
                "SELECT value FROM grades WHERE user_id = ? AND subject_id = ? ORDER BY id",
                (session["user_id"], subject["id"]),
            ).fetchall()
        ]
        average = subject_average(subject["id"])
        rows.append({
            "subject": subject,
            "grades": grades,
            "average": average,
            "final": final_grade(average),
        })
    return render_template("diary.html", rows=rows)


@app.route("/grades/add", methods=["POST"])
@login_required
def add_grade():
    db = get_db()
    subject_id = request.form.get("subject_id", type=int)
    value = request.form.get("value", type=int)
    subject = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()

    if subject is None or value not in (2, 3, 4, 5):
        flash("Выберите предмет и оценку от 2 до 5")
    else:
        db.execute(
            "INSERT INTO grades (user_id, subject_id, value) VALUES (?, ?, ?)",
            (session["user_id"], subject_id, value),
        )
        db.commit()
        flash(f"Оценка {value} по предмету «{subject['name']}» добавлена")
    return redirect(url_for("diary"))


# ---------- Домашние задания ----------

@app.route("/homework")
@login_required
def homework():
    db = get_db()
    today = date.today()
    today_text = today.strftime(DATE_FORMAT)
    items = []
    for hw in db.execute(
        "SELECT homework.*, subjects.name AS subject FROM homework "
        "JOIN subjects ON subjects.id = homework.subject_id "
        "WHERE homework.user_id = ? ORDER BY done, "
"CASE WHEN done = 0 THEN deadline END, homework.id",
        (session["user_id"],),
    ).fetchall():
        if hw["done"]:
            status, css = "Выполнено", "done"
        elif hw["deadline"] < today_text:
            status, css = "Просрочено", "late"
        elif hw["deadline"] == today_text: 
            status, css = "Сдать сегодня", "today"
        else:
            days = (parse_date(hw["deadline"]) - today).days
            status, css = f"Осталось дней: {days}", "ok"
        items.append({"hw": hw, "status": status, "css": css})

    subjects = db.execute("SELECT * FROM subjects ORDER BY id").fetchall()
    return render_template("homework.html", items=items, subjects=subjects)


@app.route("/homework/add", methods=["POST"])
@login_required
def add_homework():
    db = get_db()
    subject_id = request.form.get("subject_id", type=int)
    text = request.form.get("text", "").strip()
    subject = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    try:
        # поле <input type="date"> всегда присылает дату в виде ГГГГ-ММ-ДД
        deadline = datetime.strptime(request.form.get("deadline", ""), "%Y-%m-%d").date()
    except ValueError:
        deadline = None

    if subject is None or not text or deadline is None:
        flash("Заполните предмет, задание и срок сдачи")
    else:
        db.execute(
            "INSERT INTO homework (user_id, subject_id, text, deadline) VALUES (?, ?, ?, ?)",
            (session["user_id"], subject_id, text, deadline.strftime(DATE_FORMAT)),
        )
        db.commit()
        flash("Задание добавлено")
    return redirect(url_for("homework"))


@app.route("/homework/<int:hw_id>/done", methods=["POST"])
@login_required
def homework_done(hw_id):
    db = get_db()
    hw = db.execute(
        "SELECT * FROM homework WHERE id = ? AND user_id = ?", (hw_id, session["user_id"])
    ).fetchone()
    if hw is None:
        abort(404)
    db.execute("UPDATE homework SET done = 1 WHERE id = ?", (hw_id,))
    db.commit()
    flash("Задание отмечено как выполненное")
    return redirect(url_for("homework"))


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)