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
    conn = get_db(); cur = conn.cursor()
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
    conn.commit(); conn.close()

@app.before_first_request
def startup():
    init_db()

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

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u = request.form["username"]; p = request.form["password"]
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=? AND active=1",(u,p))
        user = cur.fetchone(); conn.close()
        if user:
            session["user_id"]=user["id"]; session["username"]=user["username"]; session["role"]=user["role"]
            return redirect("/")
        flash("Usuario/clave inválidos o inactivo")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect("/login")
    if session.get("role") == "admin":
        return redirect("/admin")
    return redirect("/funcionario")

@app.route("/admin")
@login_required(role="admin")
def admin():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM users"); users = cur.fetchall()
    cur.execute("SELECT * FROM misas ORDER BY fecha,hora"); misas = cur.fetchall()
    cur.execute("SELECT * FROM categorias"); categorias = cur.fetchall()
    cur.execute("SELECT * FROM intencion_base"); int_base = cur.fetchall()
    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'"); row = cur.fetchone()
    global_text = row["value"] if row else ""
    cur.execute("SELECT value FROM settings WHERE key='last_deletion'"); row2 = cur.fetchone()
    last_deletion = row2["value"] if row2 else "Nunca"
    conn.close()
    return render_template("admin/dashboard.html", users=users, misas=misas, categorias=categorias, int_base=int_base, global_text=global_text, last_deletion=last_deletion)

@app.route("/admin/users/create", methods=["POST"])
@login_required(role="admin")
def admin_create_user():
    u = request.form["username"]; p = request.form["password"]; role = request.form["role"]
    conn = get_db(); cur = conn.cursor()
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
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT active FROM users WHERE id=?", (user_id,)); r = cur.fetchone()
    if r:
        new = 0 if r["active"]==1 else 1
        cur.execute("UPDATE users SET active=? WHERE id=?", (new,user_id)); conn.commit()
    conn.close(); return redirect("/admin")

@app.route("/admin/users/delete/<int:user_id>")
@login_required(role="admin")
def admin_delete_user(user_id):
    conn = get_db(); cur = conn.cursor(); cur.execute("DELETE FROM users WHERE id=?", (user_id,)); conn.commit(); conn.close()
    return redirect("/admin")

@app.route("/admin/misas/create", methods=["POST"])
@login_required(role="admin")
def admin_create_misa():
    fecha = request.form["fecha"]; hora = request.form["hora"]; ampm = request.form["ampm"]
    h = hora.strip()
    try:
        dt = datetime.strptime(h, "%H:%M")
        hora_24 = dt.strftime("%H:%M")
    except:
        try:
            dt = datetime.strptime(h, "%H")
            hora_24 = dt.strftime("%H:%M")
        except:
            hora_24 = h
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO misas(fecha,hora,ampm) VALUES (?,?,?)", (fecha, hora_24, ampm))
    conn.commit(); conn.close()
    return redirect("/admin")

@app.route("/admin/misas/delete/<int:misa_id>")
@login_required(role="admin")
def admin_delete_misa(misa_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE misa_id=?", (misa_id,))
    if cur.fetchone()["c"]>0:
        flash("No se puede eliminar: hay intenciones asociadas.")
    else:
        cur.execute("DELETE FROM misas WHERE id=?", (misa_id,)); conn.commit()
    conn.close(); return redirect("/admin")

@app.route("/admin/categorias/create", methods=["POST"])
@login_required(role="admin")
def admin_create_categoria():
    nombre = request.form["nombre"]; descripcion = request.form.get("descripcion",""); texto_adicional = request.form.get("texto_adicional","")
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO categorias(nombre,descripcion,texto_adicional,active) VALUES (?,?,?,1)", (nombre,descripcion,texto_adicional))
    conn.commit(); conn.close(); return redirect("/admin")

@app.route("/admin/categorias/edit/<int:cat_id>", methods=["POST"])
@login_required(role="admin")
def admin_edit_categoria(cat_id):
    nombre = request.form["nombre"]; descripcion = request.form.get("descripcion",""); texto_adicional = request.form.get("texto_adicional","")
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE categorias SET nombre=?,descripcion=?,texto_adicional=? WHERE id=?", (nombre,descripcion,texto_adicional,cat_id))
    conn.commit(); conn.close()
    return redirect("/admin")

@app.route("/admin/categorias/delete/<int:cat_id>")
@login_required(role="admin")
def admin_delete_categoria(cat_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE categoria_id=?", (cat_id,))
    if cur.fetchone()["c"]>0:
        flash("No se puede eliminar: categoria en uso.")
    else:
        cur.execute("DELETE FROM categorias WHERE id=?", (cat_id,)); conn.commit()
    conn.close(); return redirect("/admin")

@app.route("/admin/intencion_base/create", methods=["POST"])
@login_required(role="admin")
def admin_create_int_base():
    frase = request.form["frase"]
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO intencion_base(frase,active) VALUES (?,1)", (frase,))
    conn.commit(); conn.close()
    return redirect("/admin")

@app.route("/admin/intencion_base/delete/<int:id>")
@login_required(role="admin")
def admin_delete_int_base(id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE intencion_base_id=?", (id,))
    if cur.fetchone()["c"]>0:
        flash("No se puede eliminar: frase en uso.")
    else:
        cur.execute("DELETE FROM intencion_base WHERE id=?", (id,)); conn.commit()
    conn.close(); return redirect("/admin")

@app.route("/admin/settings/pdf_text", methods=["POST"])
@login_required(role="admin")
def admin_settings_pdf_text():
    txt = request.form.get("pdf_texto_global","")
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('pdf_texto_global',?)", (txt,))
    conn.commit(); conn.close()
    return redirect("/admin")

@app.route("/admin/export_csv", methods=["POST"])
@login_required(role="admin")
def admin_export_csv():
    desde = request.form["desde"]; hasta = request.form["hasta"]
    conn = get_db(); cur = conn.cursor()
    cur.execute("""SELECT i.*, c.nombre as categoria, b.frase as int_base, u.username as funcionario, m.hora as misa_hora, m.fecha as misa_fecha
                   FROM intenciones i
                   LEFT JOIN categorias c ON c.id=i.categoria_id
                   LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
                   LEFT JOIN users u ON u.id=i.funcionario_id
                   LEFT JOIN misas m ON m.id=i.misa_id
                   WHERE date(m.fecha) BETWEEN date(?) AND date(?)""", (desde,hasta))
    rows = cur.fetchall(); conn.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["misa_fecha","misa_hora","categoria","ofrece","intencion_base","peticiones","funcionario","fecha_creado","fecha_actualizado"])
    for r in rows:
        writer.writerow([r["misa_fecha"], r["misa_hora"], r["categoria"], r["ofrece"], r["int_base"], r["peticiones"], r["funcionario"], r["fecha_creado"], r["fecha_actualizado"]])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name="intenciones_admin.csv")

@app.route("/admin/delete_range", methods=["POST"])
@login_required(role="admin")
def admin_delete_range():
    hasta = request.form["hasta"]
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key='last_deletion'"); row = cur.fetchone()
    last = row["value"] if row else None
    desde = last if last else "0001-01-01"
    cur.execute("""SELECT i.id FROM intenciones i LEFT JOIN misas m ON m.id=i.misa_id WHERE date(m.fecha) BETWEEN date(?) AND date(?)""", (desde,hasta))
    ids = [r["id"] for r in cur.fetchall()]
    for i in ids:
        cur.execute("DELETE FROM intenciones WHERE id=?", (i,))
    cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('last_deletion',?)", (hasta,))
    conn.commit(); conn.close()
    flash(f"Eliminadas intenciones hasta {hasta}")
    return redirect("/admin")

@app.route("/funcionario", methods=["GET"])
@login_required()
def funcionario():
    if session.get("role") not in ("funcionario","admin"):
        return "Acceso denegado",403
    dia = request.args.get("dia", date.today().isoformat())
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM misas WHERE fecha=? ORDER BY hora", (dia,)); misas = cur.fetchall()
    cur.execute("SELECT * FROM categorias WHERE active=1"); categorias = cur.fetchall()
    cur.execute("SELECT * FROM intencion_base WHERE active=1"); int_b = cur.fetchall()
    cur.execute("SELECT i.*, c.nombre as categoria, b.frase as int_base FROM intenciones i LEFT JOIN categorias c ON c.id=i.categoria_id LEFT JOIN intencion_base b ON b.id=i.intencion_base_id WHERE i.funcionario_id=? ORDER BY i.fecha_creado DESC", (session["user_id"],))
    propias = cur.fetchall()
    conn.close()
    return render_template("funcionario/index.html", misas=misas, categorias=categorias, int_b=int_b, propias=propias, dia=dia)

@app.route("/funcionario/registrar", methods=["POST"])
@login_required()
def funcionario_registrar():
    misa_id = int(request.form["misa_id"])
    categoria_id = int(request.form["categoria_id"])
    ofrece = request.form["ofrece"].strip()
    int_base_id = int(request.form["int_base_id"])
    peticiones = request.form["peticiones"].strip()[:250]
    ahora = datetime.now().isoformat()
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM misas WHERE id=?", (misa_id,)); misa = cur.fetchone()
    if not misa:
        flash("Misa no encontrada"); conn.close(); return redirect("/funcionario")
    if not ofrece or not peticiones:
        flash("Complete los campos requeridos"); conn.close(); return redirect("/funcionario")
    cur.execute("""INSERT INTO intenciones(misa_id,categoria_id,ofrece,intencion_base_id,peticiones,fecha_creado,fecha_actualizado,funcionario_id)
                   VALUES (?,?,?,?,?,?,?,?)""", (misa_id,categoria_id,ofrece,int_base_id,peticiones,ahora,ahora,session["user_id"]))
    conn.commit(); conn.close()
    flash("Intención registrada")
    return redirect("/funcionario")

@app.route("/funcionario/editar/<int:int_id>", methods=["GET","POST"])
@login_required()
def funcionario_editar(int_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT i.*, m.fecha as misa_fecha, m.hora as misa_hora, m.ampm FROM intenciones i LEFT JOIN misas m ON m.id=i.misa_id WHERE i.id=?", (int_id,))
    row = cur.fetchone()
    if not row or row["funcionario_id"] != session["user_id"]:
        conn.close(); return "No autorizado",403
    misa_dt = None
    try:
        hf = row["misa_hora"]
        h = datetime.strptime(hf, "%H:%M").time()
        misa_dt = datetime.combine(datetime.strptime(row["misa_fecha"], "%Y-%m-%d").date(), h)
    except:
        misa_dt = None
    if request.method=="POST":
        if misa_dt and datetime.now() > misa_dt:
            flash("No es posible editar: hora de la misa ya pasó"); conn.close(); return redirect("/funcionario")
        ofrece = request.form["ofrece"].strip(); peticiones = request.form["peticiones"].strip()[:250]; categoria_id = int(request.form["categoria_id"]); int_base_id = int(request.form["int_base_id"])
        cur.execute("UPDATE intenciones SET ofrece=?,peticiones=?,categoria_id=?,intencion_base_id=?,fecha_actualizado=? WHERE id=?", (ofrece,peticiones,categoria_id,int_base_id,datetime.now().isoformat(), int_id))
        conn.commit(); conn.close(); flash("Se guardaron los cambios"); return redirect("/funcionario")
    cur.execute("SELECT * FROM categorias WHERE active=1"); categorias = cur.fetchall()
    cur.execute("SELECT * FROM intencion_base WHERE active=1"); int_b = cur.fetchall()
    conn.close()
    return render_template("funcionario/editar.html", row=row, categorias=categorias, int_b=int_b)

@app.route("/funcionario/export_csv", methods=["POST"])
@login_required()
def funcionario_export_csv():
    desde = request.form["desde"]; hasta = request.form["hasta"]
    conn = get_db(); cur = conn.cursor()
    cur.execute("""SELECT i.*, c.nombre as categoria, b.frase as int_base, m.fecha as misa_fecha, m.hora as misa_hora
                   FROM intenciones i
                   LEFT JOIN categorias c ON c.id=i.categoria_id
                   LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
                   LEFT JOIN misas m ON m.id=i.misa_id
                   WHERE i.funcionario_id=? AND date(m.fecha) BETWEEN date(?) AND date(?)""", (session["user_id"], desde, hasta))
    rows = cur.fetchall(); conn.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["misa_fecha","misa_hora","categoria","ofrece","intencion_base","peticiones","fecha_creado","fecha_actualizado"])
    for r in rows:
        writer.writerow([r["misa_fecha"], r["misa_hora"], r["categoria"], r["ofrece"], r["int_base"], r["peticiones"], r["fecha_creado"], r["fecha_actualizado"]])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name="mis_intenciones.csv")

@app.route("/funcionario/print_day", methods=["POST"])
@login_required()
def funcionario_print_day():
    dia = request.form["dia"]
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'"); row = cur.fetchone()
    global_text = row["value"] if row else ""
    cur.execute("SELECT * FROM misas WHERE fecha=? ORDER BY hora", (dia,)); misas = cur.fetchall()
    data = []
    for m in misas:
        cur.execute("""SELECT i.*, c.nombre as categoria, c.texto_adicional as cat_text, b.frase as int_base, u.username as funcionario
                       FROM intenciones i
                       LEFT JOIN categorias c ON c.id=i.categoria_id
                       LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
                       LEFT JOIN users u ON u.id=i.funcionario_id
                       WHERE i.misa_id = ? ORDER BY i.fecha_creado""", (m["id"],))
        filas = cur.fetchall()
        items = [dict(f) for f in filas]
        data.append({"misa": m, "items": items})
    conn.close()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    w,h = letter
    y = h - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, f"INTENCIONES — Fecha: {dia}")
    y -= 30
    if global_text:
        c.setFont("Helvetica-Oblique", 10)
        for line in global_text.splitlines():
            c.drawString(50, y, line)
            y -= 12
        y -= 8
    if not data:
        c.setFont("Helvetica", 12)
        c.drawString(50, y, "No hay misas para este día.")
    else:
        for block in data:
            misa = block["misa"]
            c.setFont("Helvetica-Bold", 13)
            y -= 12
            c.drawString(50, y, f"MISA {misa['hora']} {misa['ampm']}")
            y -= 18
            items = block["items"]
            if not items:
                c.setFont("Helvetica", 11)
                c.drawString(60, y, "No hay intenciones para esta misa.")
                y -= 18
            else:
                for it in items:
                    c.setFont("Helvetica-Bold", 11)
                    c.drawString(55, y, f"[{it['categoria'].upper() if it['categoria'] else 'SIN CATEGORÍA'}]")
                    y -= 14
                    c.setFont("Helvetica", 11)
                    c.drawString(60, y, f"Ofrece: {it['ofrece']}")
                    y -= 14
                    c.drawString(60, y, f"Intención ofrecida a: {it['int_base']}")
                    y -= 14
                    pet = it['peticiones'] or ""
                    lines = []
                    while len(pet) > 90:
                        lines.append(pet[:90]); pet = pet[90:]
                    lines.append(pet)
                    for li, line in enumerate(lines):
                        if li==0:
                            c.drawString(60, y, f"Peticiones: {line}")
                        else:
                            c.drawString(60, y, f"          {line}")
                        y -= 12
                    if it.get("cat_text"):
                        c.setFont("Helvetica-Oblique", 10)
                        for line in it["cat_text"].splitlines():
                            c.drawString(60, y, line)
                            y -= 12
                        c.setFont("Helvetica", 11)
                    y -= 8
                    if y < 80:
                        c.showPage(); y = h - 50
    c.save(); buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=f"intenciones_{dia}.pdf")

@app.route("/admin/seed")
@login_required(role="admin")
def admin_seed():
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("INSERT OR IGNORE INTO categorias(nombre,descripcion,texto_adicional,active) VALUES (?,?,?,1)", ("Acción de gracias","","Dar gracias al Señor por los beneficios recibidos."))
        cur.execute("INSERT OR IGNORE INTO categorias(nombre,descripcion,texto_adicional,active) VALUES (?,?,?,1)", ("Salud","","Pedimos la fortaleza espiritual y corporal."))
        today = date.today().isoformat()
        cur.execute("INSERT INTO misas(fecha,hora,ampm) VALUES (?,?,?)", (today,"07:00","AM"))
        cur.execute("INSERT INTO misas(fecha,hora,ampm) VALUES (?,?,?)", (today,"18:00","PM"))
        cur.execute("INSERT OR IGNORE INTO intencion_base(frase,active) VALUES (?,1)", ("Por la salud de",))
        cur.execute("INSERT OR IGNORE INTO intencion_base(frase,active) VALUES (?,1)", ("Por el eterno descanso de",))
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('pdf_texto_global','Por favor mantener los celulares en modo silencio.')")
        conn.commit()
    except Exception as e:
        flash(str(e))
    conn.close(); return redirect("/admin")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000)
