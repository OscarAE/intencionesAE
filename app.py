from flask import Flask, render_template, request, redirect, send_file, session, url_for, flash, jsonify
import sqlite3, os, io, csv
from datetime import datetime, date, time as dtime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

APP_DIR = os.path.dirname(__file__)
DB = os.path.join(APP_DIR, "data.db")

app = Flask(__name__)
app.secret_key = "CAMBIAR_POR_ALGO_SEGURO"

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE,
        descripcion TEXT,
        texto_adicional TEXT,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS intencion_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        frase TEXT UNIQUE,
        active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS misas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        hora TEXT,
        ampm TEXT
    );
    CREATE TABLE IF NOT EXISTS intenciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        misa_id INTEGER,
        categoria_id INTEGER,
        ofrece TEXT,
        intencion_base_id INTEGER,
        peticiones TEXT,
        fecha_creado TEXT,
        fecha_actualizado TEXT,
        funcionario_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    cur.execute("SELECT COUNT(*) as c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute("INSERT INTO users(username,password,role,active) VALUES (?,?,?,1)",
                    ("admin","admin123","admin"))

    conn.commit()
    conn.close()

# ✅ Ejecutamos la inicialización aquí (Flask 3 ya no soporta before_first_request)
init_db()

# ------------------------------------------------------------
#  DECORADOR DE LOGIN
# ------------------------------------------------------------
def login_required(role=None):
    def decorator(f):
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                return "Acceso denegado", 403
            return f(*args, **kwargs)
        wrapped.__name__ = f.__name__
        return wrapped
    return decorator

# ------------------------------------------------------------
#  LOGIN / LOGOUT
# ------------------------------------------------------------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u = request.form["username"]
        p = request.form["password"]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=? AND active=1",(u,p))
        user = cur.fetchone()
        conn.close()

        if user:
            session["user_id"]=user["id"]
            session["username"]=user["username"]
            session["role"]=user["role"]
            return redirect("/")
        flash("Usuario/clave inválidos o inactivo")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect("/login")
    if session.get("role") == "admin":
        return redirect("/admin")
    return redirect("/funcionario")


# ------------------------------------------------------------
#  PANEL DE ADMINISTRACIÓN
# ------------------------------------------------------------
@app.route("/admin")
@login_required(role="admin")
def admin():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users")
    users = cur.fetchall()

    cur.execute("SELECT * FROM misas ORDER BY fecha,hora")
    misas = cur.fetchall()

    cur.execute("SELECT * FROM categorias")
    categorias = cur.fetchall()

    cur.execute("SELECT * FROM intencion_base")
    int_base = cur.fetchall()

    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'")
    row = cur.fetchone()
    global_text = row["value"] if row else ""

    cur.execute("SELECT value FROM settings WHERE key='last_deletion'")
    row2 = cur.fetchone()
    last_deletion = row2["value"] if row2 else "Nunca"

    conn.close()

    return render_template(
        "admin/dashboard.html",
        users=users,
        misas=misas,
        categorias=categorias,
        int_base=int_base,
        global_text=global_text,
        last_deletion=last_deletion
    )


# ------------------------------------------------------------
#  CRUD USUARIOS
# ------------------------------------------------------------
@app.route("/admin/users/create", methods=["POST"])
@login_required(role="admin")
def admin_create_user():
    u = request.form["username"]
    p = request.form["password"]
    role = request.form["role"]

    conn=get_db()
    cur=conn.cursor()
    try:
        cur.execute("INSERT INTO users(username,password,role,active) VALUES (?,?,?,1)", (u,p,role))
        conn.commit()
    except Exception as e:
        flash("Error: "+str(e))

    conn.close()
    return redirect("/admin")

@app.route("/admin/users/toggle/<int:user_id>")
@login_required(role="admin")
def admin_toggle_user(user_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT active FROM users WHERE id=?", (user_id,))
    r = cur.fetchone()

    if r:
        new = 0 if r["active"]==1 else 1
        cur.execute("UPDATE users SET active=? WHERE id=?", (new,user_id))
        conn.commit()

    conn.close()
    return redirect("/admin")

@app.route("/admin/users/delete/<int:user_id>")
@login_required(role="admin")
def admin_delete_user(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return redirect("/admin")


# ------------------------------------------------------------
#  CRUD MISAS
# ------------------------------------------------------------
@app.route("/admin/misas/create", methods=["POST"])
@login_required(role="admin")
def admin_create_misa():
    fecha = request.form["fecha"]
    hora = request.form["hora"]
    ampm = request.form["ampm"]

    try:
        dt = datetime.strptime(hora, "%H:%M")
        hora_24 = dt.strftime("%H:%M")
    except:
        hora_24 = hora

    conn=get_db()
    cur=conn.cursor()
    cur.execute("INSERT INTO misas(fecha,hora,ampm) VALUES (?,?,?)", (fecha,hora_24,ampm))
    conn.commit()
    conn.close()

    return redirect("/admin")

@app.route("/admin/misas/delete/<int:misa_id>")
@login_required(role="admin")
def admin_delete_misa(misa_id):
    conn=get_db()
    cur=conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE misa_id=?", (misa_id,))
    if cur.fetchone()["c"]>0:
        flash("No se puede eliminar: hay intenciones asociadas.")
    else:
        cur.execute("DELETE FROM misas WHERE id=?", (misa_id,))
        conn.commit()

    conn.close()
    return redirect("/admin")


# ------------------------------------------------------------
#  CRUD CATEGORIAS
# ------------------------------------------------------------
@app.route("/admin/categorias/create", methods=["POST"])
@login_required(role="admin")
def admin_create_categoria():
    nombre = request.form["nombre"]
    descripcion = request.form.get("descripcion","")
    texto_adicional = request.form.get("texto_adicional","")

    conn=get_db()
    cur=conn.cursor()
    cur.execute(
        "INSERT INTO categorias(nombre,descripcion,texto_adicional,active) VALUES (?,?,?,1)",
        (nombre,descripcion,texto_adicional)
    )
    conn.commit()
    conn.close()

    return redirect("/admin")

@app.route("/admin/categorias/edit/<int:cat_id>", methods=["POST"])
@login_required(role="admin")
def admin_edit_categoria(cat_id):
    nombre = request.form["nombre"]
    descripcion = request.form.get("descripcion","")
    texto_adicional = request.form.get("texto_adicional","")

    conn=get_db()
    cur=conn.cursor()
    cur.execute(
        "UPDATE categorias SET nombre=?,descripcion=?,texto_adicional=? WHERE id=?",
        (nombre,descripcion,texto_adicional,cat_id)
    )
    conn.commit()
    conn.close()

    return redirect("/admin")

@app.route("/admin/categorias/delete/<int:cat_id>")
@login_required(role="admin")
def admin_delete_categoria(cat_id):
    conn=get_db()
    cur=conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE categoria_id=?", (cat_id,))
    if cur.fetchone()["c"]>0:
        flash("No se puede eliminar: categoría en uso.")
    else:
        cur.execute("DELETE FROM categorias WHERE id=?", (cat_id,))
        conn.commit()

    conn.close()
    return redirect("/admin")


# ------------------------------------------------------------
#  CRUD INTENCIÓN BASE (FRAS
