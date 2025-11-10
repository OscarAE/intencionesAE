from flask import Flask, render_template, request, redirect, send_file, session, url_for, flash
import sqlite3, os, io, csv
from datetime import datetime, date
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

APP_DIR = os.path.dirname(__file__)
DB = os.path.join(APP_DIR, "data.db")

app = Flask(__name__)
app.secret_key = "CAMBIAR_POR_ALGO_SEGURO"

# ============================================================
#  FUNCIONES BASE DE BASE DE DATOS
# ============================================================

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

    # Crear el admin si no existe
    cur.execute("SELECT COUNT(*) as c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute("INSERT INTO users(username,password,role,active) VALUES (?,?,?,1)",
                    ("admin","admin123","admin"))

    conn.commit()
    conn.close()

# ✅ Ejecutar init_db() siempre al arrancar (compatible Flask 3)
init_db()

# ============================================================
#  DECORADOR LOGIN
# ============================================================

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

# ============================================================
#  LOGIN / LOGOUT
# ============================================================

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
        flash("Usuario o contraseña incorrectos")

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
# ============================================================
#  PANEL DE ADMINISTRACIÓN
# ============================================================

@app.route("/admin")
@login_required(role="admin")
def admin():
    section = request.args.get("section")

    conn = get_db(); cur = conn.cursor()

    # Usuarios
    cur.execute("SELECT * FROM users ORDER BY username")
    users = cur.fetchall()

    # Misas
    cur.execute("SELECT * FROM misas ORDER BY fecha, hora")
    misas = cur.fetchall()

    # Categorías
    cur.execute("SELECT * FROM categorias ORDER BY nombre")
    categorias = cur.fetchall()

    # Frases base
    cur.execute("SELECT * FROM intencion_base ORDER BY frase")
    frases = cur.fetchall()

    # Configuración
    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'")
    row = cur.fetchone()
    texto_global = row["value"] if row else ""

    cur.execute("SELECT value FROM settings WHERE key='last_deletion'")
    row = cur.fetchone()
    last_deletion = row["value"] if row else "Nunca"

    conn.close()

    return render_template(
        "admin/dashboard.html",
        section=section,
        users=users,
        misas=misas,
        categorias=categorias,
        frases=frases,
        texto_global=texto_global,
        last_deletion=last_deletion
    )


# ============================================================
#  CRUD USUARIOS
# ============================================================

@app.route("/admin/users/create", methods=["POST"])
@login_required(role="admin")
def admin_create_user():
    u = request.form["username"]
    p = request.form["password"]
    role = request.form["role"]

    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users(username,password,role,active) VALUES (?,?,?,1)", (u,p,role))
        conn.commit()
    except Exception as e:
        flash("Error: " + str(e))
    conn.close()

    return redirect("/admin")

@app.route("/admin/users/toggle/<int:user_id>")
@login_required(role="admin")
def admin_toggle_user(user_id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT active FROM users WHERE id=?", (user_id,))
    r = cur.fetchone()

    if r:
        new = 0 if r["active"] == 1 else 1
        cur.execute("UPDATE users SET active=? WHERE id=?", (new, user_id))
        conn.commit()

    conn.close()
    return redirect("/admin")

@app.route("/admin/users/delete/<int:user_id>")
@login_required(role="admin")
def admin_delete_user(user_id):

    # impedir que se elimine a sí mismo
    if session["user_id"] == user_id:
        flash("No puede eliminarse a usted mismo.")
        return redirect("/admin")

    conn = get_db()
    cur = conn.cursor()

    # obtener información del usuario
    cur.execute("SELECT role, active FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()

    if not row:
        flash("Usuario no encontrado.")
        conn.close()
        return redirect("/admin")

    # impedir eliminar administradores
    if row["role"] == "admin":
        flash("No es posible eliminar administradores. Solo se puede activar/inactivar.")
        conn.close()
        return redirect("/admin")

    # eliminar (solo funcionarios)
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    flash("Usuario eliminado correctamente.")
    return redirect("/admin")


# ============================================================
#  CRUD MISAS
# ============================================================

@app.route("/admin/misas/create", methods=["POST"])
@login_required(role="admin")
def admin_create_misa():
    fecha = request.form["fecha"]
    hora = request.form["hora"]
    ampm = request.form["ampm"]

    # Convertir formato 4 dígitos a HH:MM
    h = hora.strip()

    if len(h) == 4 and h.isdigit():
        # Ejemplo: 0700 → 07:00
        h = h[:2] + ":" + h[2:]

    try:
        dt = datetime.strptime(h, "%H:%M")
        hora_24 = dt.strftime("%H:%M")
    except:
        flash("Formato de hora inválido. Use 4 números (ej: 0700).")
        return redirect("/admin")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO misas(fecha,hora,ampm) VALUES (?,?,?)", (fecha, hora_24, ampm))
    conn.commit()
    conn.close()

    return redirect("/admin")


@app.route("/admin/misas/delete/<int:misa_id>")
@login_required(role="admin")
def admin_delete_misa(misa_id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE misa_id=?", (misa_id,))
    if cur.fetchone()["c"] > 0:
        flash("No se puede eliminar: hay intenciones asociadas.")
    else:
        cur.execute("DELETE FROM misas WHERE id=?", (misa_id,))
        conn.commit()

    conn.close()
    return redirect("/admin")

# ============================================================
#  CRUD CATEGORÍAS
# ============================================================

@app.route("/admin/categorias/create", methods=["POST"])
@login_required(role="admin")
def admin_create_categoria():
    nombre = request.form["nombre"]
    descripcion = request.form.get("descripcion", "")
    texto_adicional = request.form.get("texto_adicional", "")

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO categorias(nombre,descripcion,texto_adicional,active) VALUES (?,?,?,1)",
        (nombre, descripcion, texto_adicional)
    )
    conn.commit()
    conn.close()

    return redirect("/admin")

@app.route("/admin/categorias/edit/<int:cat_id>", methods=["POST"])
@login_required(role="admin")
def admin_edit_categoria(cat_id):
    nombre = request.form["nombre"]
    descripcion = request.form.get("descripcion", "")
    texto_adicional = request.form.get("texto_adicional", "")

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE categorias SET nombre=?, descripcion=?, texto_adicional=? WHERE id=?",
        (nombre, descripcion, texto_adicional, cat_id)
    )
    conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/admin/categorias/delete/<int:cat_id>")
@login_required(role="admin")
def admin_delete_categoria(cat_id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE categoria_id=?", (cat_id,))
    if cur.fetchone()["c"] > 0:
        flash("No se puede eliminar: categoría en uso.")
    else:
        cur.execute("DELETE FROM categorias WHERE id=?", (cat_id,))
        conn.commit()

    conn.close()
    return redirect("/admin")

# ============================================================
# CRUD INTENCIÓN BASE
# ============================================================

@app.route("/admin/intencion_base/create", methods=["POST"])
@login_required(role="admin")
def admin_create_int_base():
    frase = request.form["frase"]

    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT INTO intencion_base(frase,active) VALUES (?,1)", (frase,))
    conn.commit()
    conn.close()
    return redirect("/admin")

@app.route("/admin/intencion_base/delete/<int:id>")
@login_required(role="admin")
def admin_delete_int_base(id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE intencion_base_id=?", (id,))
    if cur.fetchone()["c"] > 0:
        flash("No se puede eliminar: frase en uso.")
    else:
        cur.execute("DELETE FROM intencion_base WHERE id=?", (id,))
        conn.commit()

    conn.close()
    return redirect("/admin")
# ============================================================
#  CONFIGURACIÓN Y AJUSTES (PDF, RANGOS, ETC)
# ============================================================

@app.route("/admin/settings/pdf_text", methods=["POST"])
@login_required(role="admin")
def admin_settings_pdf_text():
    txt = request.form.get("pdf_texto_global", "")
    conn = get_db(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('pdf_texto_global',?)", (txt,))
    conn.commit()
    conn.close()
    return redirect("/admin")


# ============================================================
#  EXPORTAR CSV (ADMIN)
# ============================================================

@app.route("/admin/export_csv", methods=["POST"])
@login_required(role="admin")
def admin_export_csv():
    desde = request.form["desde"]
    hasta = request.form["hasta"]

    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT i.*, c.nombre as categoria, b.frase as int_base,
               u.username as funcionario, m.hora as misa_hora, m.fecha as misa_fecha
        FROM intenciones i
        LEFT JOIN categorias c ON c.id=i.categoria_id
        LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
        LEFT JOIN users u ON u.id=i.funcionario_id
        LEFT JOIN misas m ON m.id=i.misa_id
        WHERE date(m.fecha) BETWEEN date(?) AND date(?)
        ORDER BY misa_fecha, misa_hora
    """, (desde, hasta))

    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["misa_fecha","misa_hora","categoria","ofrece",
                     "intencion_base","peticiones","funcionario",
                     "fecha_creado","fecha_actualizado"])

    for r in rows:
        writer.writerow([
            r["misa_fecha"], r["misa_hora"], r["categoria"], r["ofrece"],
            r["int_base"], r["peticiones"], r["funcionario"],
            r["fecha_creado"], r["fecha_actualizado"]
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="intenciones_admin.csv"
    )

# ============================================================
#  BORRAR INTENCIONES POR RANGO
# ============================================================

@app.route("/admin/delete_range", methods=["POST"])
@login_required(role="admin")
def admin_delete_range():
    hasta = request.form["hasta"]

    conn = get_db(); cur = conn.cursor()

    # identificar última eliminación
    cur.execute("SELECT value FROM settings WHERE key='last_deletion'")
    row = cur.fetchone()
    desde = row["value"] if row else "0001-01-01"

    # obtener intenciones del rango
    cur.execute("""
        SELECT i.id
        FROM intenciones i
        LEFT JOIN misas m ON m.id=i.misa_id
        WHERE date(m.fecha) BETWEEN date(?) AND date(?)
    """, (desde, hasta))

    ids = [r["id"] for r in cur.fetchall()]

    # borrar
    for i in ids:
        cur.execute("DELETE FROM intenciones WHERE id=?", (i,))

    cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('last_deletion',?)", (hasta,))
    conn.commit()
    conn.close()

    flash(f"Eliminadas intenciones hasta {hasta}")
    return redirect("/admin")

# ============================================================
#  PANEL FUNCIONARIO
# ============================================================

@app.route("/funcionario")
@login_required()
def funcionario():
    if session.get("role") not in ("funcionario","admin"):
        return "Acceso denegado", 403

    dia = request.args.get("dia", date.today().isoformat())

    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT * FROM misas WHERE fecha=? ORDER BY hora", (dia,))
    misas = cur.fetchall()

    cur.execute("SELECT * FROM categorias WHERE active=1 ORDER BY nombre")
    categorias = cur.fetchall()

    cur.execute("SELECT * FROM intencion_base WHERE active=1 ORDER BY frase")
    int_b = cur.fetchall()

    cur.execute("""
        SELECT i.*, c.nombre AS categoria, b.frase AS int_base
        FROM intenciones i
        LEFT JOIN categorias c ON c.id=i.categoria_id
        LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
        WHERE i.funcionario_id=?
        ORDER BY i.fecha_creado DESC
    """, (session["user_id"],))
    propias = cur.fetchall()

    conn.close()

    return render_template(
        "funcionario/index.html",
        misas=misas,
        categorias=categorias,
        int_b=int_b,
        propias=propias,
        dia=dia
    )

# ============================================================
#  REGISTRAR INTENCIÓN
# ============================================================

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

    cur.execute("SELECT * FROM misas WHERE id=?", (misa_id,))
    misa = cur.fetchone()

    if not misa:
        flash("Misa no encontrada")
        return redirect("/funcionario")

    if not ofrece or not peticiones:
        flash("Debe completar todos los campos")
        return redirect("/funcionario")

    cur.execute("""
        INSERT INTO intenciones(misa_id,categoria_id,ofrece,intencion_base_id,
                                peticiones,fecha_creado,fecha_actualizado,
                                funcionario_id)
        VALUES (?,?,?,?,?,?,?,?)
    """, (misa_id, categoria_id, ofrece, int_base_id, peticiones,
          ahora, ahora, session["user_id"]))

    conn.commit()
    conn.close()

    flash("Intención registrada exitosamente")
    return redirect("/funcionario")

# ============================================================
#  EDITAR INTENCIÓN
# ============================================================

@app.route("/funcionario/editar/<int:int_id>", methods=["GET","POST"])
@login_required()
def funcionario_editar(int_id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("""
        SELECT i.*, m.fecha as misa_fecha, m.hora as misa_hora
        FROM intenciones i
        LEFT JOIN misas m ON m.id=i.misa_id
        WHERE i.id=?
    """, (int_id,))
    row = cur.fetchone()

    if not row or row["funcionario_id"] != session["user_id"]:
        conn.close()
        return "No autorizado", 403

    misa_dt = datetime.combine(
        datetime.strptime(row["misa_fecha"], "%Y-%m-%d").date(),
        datetime.strptime(row["misa_hora"], "%H:%M").time()
    )

    if request.method == "POST":
        # no permitir editar después de la misa
        if datetime.now() > misa_dt:
            flash("La misa ya pasó, no se puede editar")
            conn.close()
            return redirect("/funcionario")

        ofrece = request.form["ofrece"][:200]
        peticiones = request.form["peticiones"][:250]
        categoria_id = int(request.form["categoria_id"])
        int_base_id = int(request.form["int_base_id"])

        cur.execute("""
            UPDATE intenciones
            SET ofrece=?, peticiones=?, categoria_id=?, intencion_base_id=?,
                fecha_actualizado=?
            WHERE id=?
        """, (ofrece, peticiones, categoria_id, int_base_id,
              datetime.now().isoformat(), int_id))

        conn.commit()
        conn.close()
        flash("Cambios guardados")
        return redirect("/funcionario")

    # cargar combos
    cur.execute("SELECT * FROM categorias WHERE active=1 ORDER BY nombre")
    categorias = cur.fetchall()

    cur.execute("SELECT * FROM intencion_base WHERE active=1 ORDER BY frase")
    int_b = cur.fetchall()

    conn.close()

    return render_template(
        "funcionario/editar.html",
        row=row,
        categorias=categorias,
        int_b=int_b
    )

# ============================================================
#  EXPORTAR CSV FUNCIONARIO
# ============================================================

@app.route("/funcionario/export_csv", methods=["POST"])
@login_required()
def funcionario_export_csv():
    desde = request.form["desde"]
    hasta = request.form["hasta"]

    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT i.*, c.nombre as categoria, b.frase as int_base,
               m.fecha as misa_fecha, m.hora as misa_hora
        FROM intenciones i
        LEFT JOIN categorias c ON c.id=i.categoria_id
        LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
        LEFT JOIN misas m ON m.id=i.misa_id
        WHERE i.funcionario_id=? AND date(m.fecha) BETWEEN date(?) AND date(?)
    """, (session["user_id"], desde, hasta))

    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["misa_fecha","misa_hora","categoria","ofrece",
                     "intencion_base","peticiones",
                     "fecha_creado","fecha_actualizado"])

    for r in rows:
        writer.writerow([
            r["misa_fecha"], r["misa_hora"], r["categoria"], r["ofrece"],
            r["int_base"], r["peticiones"],
            r["fecha_creado"], r["fecha_actualizado"]
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="mis_intenciones.csv"
    )

# ============================================================
#  GENERAR PDF POR DÍA
# ============================================================

@app.route("/funcionario/print_day", methods=["POST"])
@login_required()
def funcionario_print_day():
    dia = request.form["dia"]

    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'")
    row = cur.fetchone()
    global_text = row["value"] if row else ""

    cur.execute("SELECT * FROM misas WHERE fecha=? ORDER BY hora", (dia,))
    misas = cur.fetchall()

    # Preparar PDF
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    w, h = letter
    y = h - 40

    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, f"INTENCIONES — {dia}")
    y -= 30

    if global_text:
        c.setFont("Helvetica-Oblique", 10)
        for line in global_text.splitlines():
            c.drawString(50, y, line)
            y -= 14
        y -= 10

    for misa in misas:
        c.setFont("Helvetica-Bold", 13)
        c.drawString(50, y, f"MISA {misa['hora']} {misa['ampm']}")
        y -= 20

        cur.execute("""
            SELECT i.*, c.nombre AS cat, c.texto_adicional AS cat_text,
                   b.frase AS base
            FROM intenciones i
            LEFT JOIN categorias c ON c.id=i.categoria_id
            LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
            WHERE i.misa_id=?
            ORDER BY i.fecha_creado ASC
        """, (misa["id"],))

        items = cur.fetchall()

        if not items:
            c.setFont("Helvetica", 11)
            c.drawString(70, y, "No hay intenciones.")
            y -= 25
            continue

        for it in items:
            c.setFont("Helvetica-Bold", 11)
            c.drawString(60, y, f"[{it['cat']}]")
            y -= 15

            c.setFont("Helvetica", 11)
            c.drawString(70, y, f"Ofrece: {it['ofrece']}")
            y -= 14

            c.drawString(70, y, f"Intención: {it['base']}")
            y -= 14

            # dividir texto largo
            pet = it["peticiones"] or ""
            while len(pet) > 90:
                c.drawString(70, y, "Peticiones: " + pet[:90])
                y -= 12
                pet = pet[90:]
            c.drawString(70, y, "Peticiones: " + pet)
            y -= 16

            # texto adicional categoría
            if it["cat_text"]:
                c.setFont("Helvetica-Oblique", 10)
                for line in it["cat_text"].splitlines():
                    c.drawString(70, y, line)
                    y -= 12
                c.setFont("Helvetica", 11)
                y -= 10

            if y < 100:
                c.showPage()
                y = h - 40

        y -= 20

    c.save()
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"intenciones_{dia}.pdf"
    )

# ============================================================
#  EJECUCIÓN LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
