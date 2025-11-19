from flask import Flask, render_template, request, redirect, send_file, session, url_for, flash
import sqlite3, os, io, csv
from datetime import datetime, timedelta, date
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
        active INTEGER DEFAULT 1,
        orden INTEGER DEFAULT 0
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
        flash("❌ Usuario o contraseña incorrectos.")

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
    cur.execute("""
    SELECT *
    FROM misas
    ORDER BY 
        fecha ASC,
        CAST(
            CASE
                WHEN ampm = 'AM' AND substr(hora,1,2) = '12' THEN 0
                WHEN ampm = 'PM' AND substr(hora,1,2) != '12' THEN CAST(substr(hora,1,2) AS INTEGER) + 12
                ELSE CAST(substr(hora,1,2) AS INTEGER)
            END
        AS INTEGER),
        CAST(substr(hora,4,2) AS INTEGER)
    """)
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
    
    flash("✅ Usuario creado exitosamente.")
    return redirect("/admin")

@app.route("/admin/users/toggle/<int:user_id>")
@login_required(role="admin")
def admin_toggle_user(user_id):

    conn = get_db()
    cur = conn.cursor()

    # obtener info del usuario a cambiar
    cur.execute("SELECT role, active FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()

    if not row:
        flash("❌ Usuario no encontrado.")
        conn.close()
        return redirect("/admin")

    role = row["role"]
    active = row["active"]

    # impedir que un admin se desactive a sí mismo
    if session["user_id"] == user_id:
        flash("❌ No puede inactivarse a usted mismo.")
        conn.close()
        return redirect("/admin")

    # si es admin, verificar que no se deje el sistema sin administradores
    if role == "admin":
        # contar admins activos
        cur.execute("SELECT COUNT(*) as c FROM users WHERE role='admin' AND active=1")
        count_admins = cur.fetchone()["c"]

        # si solo hay 1 admin activo, no se puede inactivar
        if count_admins <= 1 and active == 1:
            flash("❌ No se puede inactivar este administrador. Debe haber al menos un administrador activo.")
            conn.close()
            return redirect("/admin")

    # cambiar estado
    new = 0 if active == 1 else 1
    cur.execute("UPDATE users SET active=? WHERE id=?", (new, user_id))
    conn.commit()
    conn.close()

    flash("✅ Estado actualizado.")
    return redirect("/admin")


@app.route("/admin/users/delete/<int:user_id>")
@login_required(role="admin")
def admin_delete_user(user_id):

    # impedir que se elimine a sí mismo
    if session["user_id"] == user_id:
        flash("❌ No puede eliminarse a usted mismo.")
        return redirect("/admin")

    conn = get_db()
    cur = conn.cursor()

    # obtener información del usuario
    cur.execute("SELECT role, active FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()

    if not row:
        flash("❌ Usuario no encontrado.")
        conn.close()
        return redirect("/admin")

    # impedir eliminar administradores
    if row["role"] == "admin":
        flash("❌ No es posible eliminar administradores. Solo se puede activar/inactivar.")
        conn.close()
        return redirect("/admin")

    # eliminar (solo funcionarios)
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    flash("✅ Usuario eliminado correctamente.")
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
        flash("❌ Formato de hora inválido. Use 4 números (ej: 0700).")
        return redirect("/admin")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO misas(fecha,hora,ampm) VALUES (?,?,?)", (fecha, hora_24, ampm))
    conn.commit()
    conn.close()
    
    flash("✅ Misa creada exitosamente.")
    return redirect("/admin")


@app.route("/admin/misas/delete/<int:misa_id>")
@login_required(role="admin")
def admin_delete_misa(misa_id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE misa_id=?", (misa_id,))
    if cur.fetchone()["c"] > 0:
        flash("❌ No se puede eliminar: hay intenciones asociadas.")
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
    orden = request.form.get("orden", 0)  # 🆕 Campo nuevo

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO categorias(nombre, descripcion, texto_adicional, orden, active) VALUES (?,?,?,?,1)",
        (nombre, descripcion, texto_adicional, orden)
    )
    conn.commit()
    conn.close()

    flash("✅ Categoría creada exitosamente.")
    return redirect("/admin")


@app.route("/admin/categorias/edit/<int:cat_id>", methods=["POST"])
@login_required(role="admin")
def admin_edit_categoria(cat_id):
    nombre = request.form["nombre"]
    descripcion = request.form.get("descripcion", "")
    texto_adicional = request.form.get("texto_adicional", "")
    orden = request.form.get("orden", 0)  # 🆕 Campo nuevo

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE categorias SET nombre=?, descripcion=?, texto_adicional=?, orden=? WHERE id=?",
        (nombre, descripcion, texto_adicional, orden, cat_id)
    )
    conn.commit()
    conn.close()

    flash("✅ Categoría actualizada correctamente.")
    return redirect("/admin")


@app.route("/admin/categorias/delete/<int:cat_id>")
@login_required(role="admin")
def admin_delete_categoria(cat_id):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE categoria_id=?", (cat_id,))
    if cur.fetchone()["c"] > 0:
        flash("❌ No se puede eliminar: categoría en uso.")
    else:
        cur.execute("DELETE FROM categorias WHERE id=?", (cat_id,))
        conn.commit()

    conn.close()
    flash("🗑️ Categoría eliminada correctamente.")
    return redirect("/admin")

# ============================================================
# CRUD INTENCIÓN BASE
# ============================================================

@app.route("/admin/intencion_base/create", methods=["POST"])
@login_required(role="admin")
def admin_create_int_base():
    frase = request.form["frase"].strip()

    conn = get_db()
    cur = conn.cursor()

    # Verificar si ya existe una frase igual (ignorando mayúsculas/minúsculas)
    cur.execute("SELECT 1 FROM intencion_base WHERE LOWER(frase) = LOWER(?)", (frase,))
    existe = cur.fetchone()

    if existe:
        conn.close()
        flash("❌ Ya existe una intención base con esa frase. Intente con otra.", "error")
        return redirect("/admin")

    # Si no existe, la inserta normalmente
    cur.execute("INSERT INTO intencion_base(frase, active) VALUES (?, 1)", (frase,))
    conn.commit()
    conn.close()

    flash("✅ Intención creada exitosamente.", "success")
    return redirect("/admin")

@app.route("/admin/intencion_base/delete/<int:id>")
@login_required(role="admin")
def admin_delete_int_base(id):
    conn = get_db(); cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM intenciones WHERE intencion_base_id=?", (id,))
    if cur.fetchone()["c"] > 0:
        flash("❌ No se puede eliminar: frase en uso.")
    else:
        cur.execute("DELETE FROM intencion_base WHERE id=?", (id,))
        conn.commit()

    conn.close()

    flash("✅ Intencion eliminada creada exitosamente.")
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

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT i.*, 
               c.nombre AS categoria, 
               b.frase AS int_base,
               u.username AS funcionario, 
               m.hora AS misa_hora, 
               m.fecha AS misa_fecha
        FROM intenciones i
        LEFT JOIN categorias c ON c.id = i.categoria_id
        LEFT JOIN intencion_base b ON b.id = i.intencion_base_id
        LEFT JOIN users u ON u.id = i.funcionario_id
        LEFT JOIN misas m ON m.id = i.misa_id
        WHERE m.fecha >= ? 
          AND m.fecha <= ?
        ORDER BY m.fecha, m.hora
    """, (desde, hasta))

    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    # encabezados
    writer.writerow([
        "Fecha Misa", "Hora", "Categoría", "Ofrece",
        "Frase Base", "Peticiones", "Funcionario",
        "Fecha Creado", "Fecha Actualizado"
    ])

    # filas
    for r in rows:
        writer.writerow([
            r["misa_fecha"], 
            r["misa_hora"], 
            r["categoria"], 
            r["ofrece"],
            r["int_base"], 
            r["peticiones"], 
            r["funcionario"],
            r["fecha_creado"], 
            r["fecha_actualizado"]
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
    if session.get("role") not in ("funcionario", "admin"):
        return "Acceso denegado", 403

    dia = request.args.get("dia", date.today().isoformat())

    conn = get_db()
    cur = conn.cursor()

    # Consultar misas ordenadas por hora y AM/PM correctamente
    cur.execute("""
        SELECT *
        FROM misas
        WHERE fecha=?
        ORDER BY 
            CASE ampm WHEN 'AM' THEN 0 ELSE 1 END,
            substr(hora,1,2) + 0,
            substr(hora,4,2) + 0
    """, (dia,))
    misas = cur.fetchall()

    # Categorías activas
    cur.execute("SELECT * FROM categorias WHERE active=1 ORDER BY nombre")
    categorias = cur.fetchall()

    # Intenciones base activas
    cur.execute("SELECT * FROM intencion_base WHERE active=1 ORDER BY frase")
    int_b = cur.fetchall()

    # Intenciones propias del funcionario
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

    flash("✅ Intención registrada exitosamente.")
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
            flash("❌ La misa ya pasó, no se puede editar.")
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
        flash("✅ Cambios guardados.")
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
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from datetime import datetime
    from textwrap import wrap
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas as rl_canvas
    import locale, io
    from flask import send_file, session

    dia = request.form["dia"]
    conn = get_db()
    cur = conn.cursor()

    # === TEXTO GLOBAL ===
    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'")
    row = cur.fetchone()
    global_text = row["value"].strip() if row else ""

    # === MISAS DEL DÍA ===
    cur.execute("SELECT * FROM misas WHERE fecha=? ORDER BY hora", (dia,))
    misas = cur.fetchall()

    # ======= Helpers =======
    line_height = 10
    footer_limit = 120

    # Paragraph styles
    cell_style = ParagraphStyle(name="CellStyle", fontName="Helvetica", fontSize=8, leading=10)
    small_style = ParagraphStyle(name="SmallStyle", fontName="Helvetica", fontSize=7, leading=9)
    header_style = ParagraphStyle(name="HeaderStyle", fontName="Helvetica-Bold", fontSize=9, alignment=1, leading=11)

    dias = {"Monday": "LUNES","Tuesday": "MARTES","Wednesday": "MIÉRCOLES",
            "Thursday": "JUEVES","Friday": "VIERNES","Saturday": "SÁBADO","Sunday": "DOMINGO"}
    meses = {"January": "ENERO","February": "FEBRERO","March": "MARZO","April": "ABRIL",
             "May": "MAYO","June": "JUNIO","July": "JULIO","August": "AGOSTO",
             "September": "SEPTIEMBRE","October": "OCTUBRE","November": "NOVIEMBRE","December": "DICIEMBRE"}

    fecha_dt = datetime.strptime(dia, "%Y-%m-%d")
    fecha_formateada = f"{dias[fecha_dt.strftime('%A')]} {fecha_dt.day} DE {meses[fecha_dt.strftime('%B')]} DE {fecha_dt.year}"

    # ======= Primera pasada: contar páginas =======
    class CountingCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.page_count = 1

        def showPage(self):
            self.page_count += 1
            super().showPage()

    # Encabezado y pie
    def fondo_encabezado_on(c):
        try:
            c.saveState()
            c.drawImage("static/borde.png", 0, 0, width=w, height=h, mask="auto")
            c.drawImage("static/titulo.png", (w - 400) / 2, h - 110,
                        width=400, height=65, mask="auto")
            c.restoreState()
        except:
            pass

    def pie_pagina_on(c, num, total):
        usuario = session.get("username", "N/A")
        # Ajustamos zona horaria Colombia (UTC-5)
        now = datetime.utcnow() - timedelta(hours=5)
    
        dia_imp = dias[now.strftime("%A")]
        mes_imp = meses[now.strftime("%B")]
        hora_imp = now.strftime("%I:%M %p").upper()
        fecha_imp = f"{dia_imp} {now.day} DE {mes_imp} DE {now.year} A LAS {hora_imp}"
    
        c.setFont("Helvetica", 8)
        c.setFillGray(0.3)
        c.drawString(100, 55, f"IMPRESO POR: {usuario} — {fecha_imp}")
        c.drawRightString(w - 100, 55, f"Página {num} de {total}")
        c.setFillGray(0)

    # ======= Renderizador general (ambas pasadas) =======
    def render_content(c, count_only=False, page_state=None, total_pages=None):
        nonlocal w, h
    
        fondo_encabezado_on(c)

        # 🔥 TÍTULO SOLO EN LA PRIMERA PÁGINA REAL
        if count_only or (not count_only and page_state['current'] == 1 and c.getPageNumber() == 1):
            c.setFont("Helvetica-Bold", 11)
            c.drawCentredString(w/2, h - 130, f"INTENCIONES PARA LA SANTA MISA — {fecha_formateada}")
    
        y_loc = h - 160
        c.setFont("Helvetica", 8)
    
        def make_new_page():
            nonlocal y_loc
    
            if count_only:
                c.showPage()
                fondo_encabezado_on(c)
                # En la pasada de conteo: SIEMPRE dibujar título (porque solo estamos midiendo)
                c.setFont("Helvetica-Bold", 11)
                c.drawCentredString(w/2, h - 130, f"INTENCIONES PARA LA SANTA MISA — {fecha_formateada}")
                y_loc = h - 160
                c.setFont("Helvetica", 8)
    
            else:
                # Pie de página
                pie_pagina_on(c, page_state['current'], total_pages)
                c.showPage()
                fondo_encabezado_on(c)
    
                # SOLO MOSTRAR EN LA PRIMERA PÁGINA REAL
                if page_state['current'] == 1:
                    c.setFont("Helvetica-Bold", 11)
                    c.drawCentredString(w/2, h - 130, f"INTENCIONES PARA LA SANTA MISA — {fecha_formateada}")
    
                page_state['current'] += 1
                y_loc = h - 160
                c.setFont("Helvetica", 8)

        # ==== Recorrer misas ====
        for misa in misas:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(50, y_loc, f"MISA {misa['hora']} {misa['ampm']}")
            y_loc -= 18

            cur.execute("""
                SELECT i.*, c.nombre AS cat, c.texto_adicional AS cat_text, b.frase AS base
                FROM intenciones i
                LEFT JOIN categorias c ON c.id=i.categoria_id
                LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
                WHERE i.misa_id=?
                ORDER BY c.orden ASC, i.fecha_creado ASC
            """, (misa["id"],))
            items = cur.fetchall()

            if not items:
                c.setFont("Helvetica", 10)
                c.drawString(70, y_loc, "No hay intenciones registradas.")
                y_loc -= 25
                continue

            # Agrupar categorías
            categorias_local = []
            for it in items:
                cat = it["cat_text"] or it["cat"] or "SIN CATEGORÍA"
                if not categorias_local or categorias_local[-1][0] != cat:
                    categorias_local.append((cat, [it]))
                else:
                    categorias_local[-1][1].append(it)

            # Categorías
            for cat_nombre, cat_items in categorias_local:
                nombre_upper = (cat_nombre or "").upper().strip()
                cat_real = (cat_items[0]["cat"] or "").upper().strip()

                c.setFont("Helvetica-Bold", 10)
                c.drawString(50, y_loc, nombre_upper)
                y_loc -= 15

                # === DIFUNTOS ===
                if "DIFUNT" in cat_real or "DIFUNT" in nombre_upper:
                
                    # Construir filas de la tabla 3 columnas
                    data = []
                    fila = []
                
                    for it in cat_items:
                        fila.append(Paragraph(it["peticiones"] or "", small_style))
                        if len(fila) == 3:
                            data.append(fila)
                            fila = []
                
                    # Completar última fila si está incompleta
                    if fila:
                        while len(fila) < 3:
                            fila.append(Paragraph("", small_style))
                        data.append(fila)
                
                    # 🔥 FORZAR SIEMPRE 20 FILAS llenando con vacías
                    while len(data) < 20:
                        data.append([
                            Paragraph("", small_style),
                            Paragraph("", small_style),
                            Paragraph("", small_style)
                        ])
                
                    # Medidas
                    x_ini = 2 * cm
                    col_width = (w - 4 * cm) / 3
                
                    # 🔥 ALTO FIJO PARA TODAS LAS FILAS
                    row_height = 14   # ajustable
                
                    t = Table(
                        data,
                        colWidths=[col_width] * 3,
                        rowHeights=[row_height] * len(data)   # 🔥 aquí se fija
                    )
                
                    t.setStyle(TableStyle([
                        ("GRID", (0,0), (-1,-1), 0.25, colors.lightgrey),
                        ("VALIGN", (0,0), (-1,-1), "TOP"),
                    ]))
                
                    # Calcular tamaño
                    w_table, h_table = t.wrapOn(c, w - 4*cm, y_loc)
                
                    # Saltar página si no cabe
                    if y_loc - h_table < footer_limit:
                        make_new_page()
                
                    # Dibujar
                    t.drawOn(c, x_ini, y_loc - h_table)
                    y_loc -= h_table + 20
                
                    continue

                # === SALUD ===
                elif "SALUD" in nombre_upper:
                    texto = ", ".join([it["peticiones"] for it in cat_items])
                    wrapped = wrap(texto, 100)
                    needed_h = len(wrapped)*line_height + 20
                    if y_loc - needed_h < footer_limit:
                        make_new_page()
                    c.setFont("Helvetica", 8)
                    for line in wrapped:
                        c.drawString(60, y_loc, line)
                        y_loc -= line_height
                    y_loc -= 10
                    continue

                # === ACCIÓN DE GRACIAS ===
                elif "GRACIAS" in nombre_upper:
                    data = [[Paragraph("PETICIONES", header_style),
                             Paragraph("OFRECE", header_style)]]
                    for it in cat_items:
                        data.append([
                            Paragraph(it["peticiones"] or "", cell_style),
                            Paragraph(it["ofrece"] or "", cell_style)
                        ])
                    t = Table(data, colWidths=[(w-100)/2,(w-100)/2])
                    t.setStyle(TableStyle([
                        ('GRID',(0,0),(-1,-1),0.5,colors.black),
                        ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
                    ]))
                    w_table, h_table = t.wrapOn(c, w - 100, y_loc)
                    if y_loc - h_table < footer_limit:
                        make_new_page()
                    t.drawOn(c, 50, y_loc - h_table)
                    y_loc -= h_table + 20
                    continue

                # === VARIOS / OTRAS ===
                else:
                    for it in cat_items:
                        txt = (it["peticiones"] or "").strip()
                        if it["ofrece"]:
                            full = f"• {txt} — OFRECE: {it['ofrece']}"
                        else:
                            full = f"• {txt}"

                        wrapped_item = wrap(full, 100)
                        needed_h = len(wrapped_item)*line_height + 15
                        if y_loc - needed_h < footer_limit:
                            make_new_page()
                        c.setFont("Helvetica", 8)
                        for line in wrapped_item:
                            c.drawString(60, y_loc, line)
                            y_loc -= line_height
                        y_loc -= 5
                    y_loc -= 10

        # ===== TEXTO GLOBAL FINAL =====
        if global_text:
            y_loc -= 20
        
            margen_x = 2 * cm
            ancho_texto = w - 4 * cm  # márgenes laterales 2cm
        
            # Título centrado
            c.setFont("Helvetica-Bold", 10)
            needed_h = 30
            if y_loc - needed_h < footer_limit:
                make_new_page()
        
            #c.drawCentredString(w/2, y_loc, "INTENCIONES ESPECIALES")
            #y_loc -= 25
        
            # Texto global (negrilla, centrado)
            c.setFont("Helvetica-Bold", 9)
        
            # Envolver respetando ancho permitido
            wrapped_lines = wrap(" ".join(global_text.splitlines()),
                                 width=int(ancho_texto / 5.5))
        
            needed_h = len(wrapped_lines) * 12 + 10
            if y_loc - needed_h < footer_limit:
                make_new_page()
        
            for line in wrapped_lines:
                c.drawCentredString(w/2, y_loc, line)
                y_loc -= 12

        return

    # ===== PASADA 1 =====
    buf_count = io.BytesIO()
    counting = CountingCanvas(buf_count, pagesize=letter)
    w, h = letter
    render_content(counting, count_only=True)
    counting.save()
    total_pages = counting.page_count - 1

    # ===== PASADA 2 =====
    buf_final = io.BytesIO()
    final_canvas = rl_canvas.Canvas(buf_final, pagesize=letter)
    page_state = {'current': 1}
    render_content(final_canvas, count_only=False, page_state=page_state, total_pages=total_pages)
    pie_pagina_on(final_canvas, page_state['current'], total_pages)
    final_canvas.save()

    buf_final.seek(0)
    return send_file(buf_final, mimetype="application/pdf",
                     as_attachment=True,
                     download_name=f"intenciones_{dia}.pdf")

# ============================================================
#  VERIFICA AL DB
# ============================================================
@app.route("/admin/debug_int_raw2")
@login_required(role="admin")
def debug_int_raw2():
    conn = get_db()
    cur = conn.cursor()

    # Ver las columnas
    cur.execute("PRAGMA table_info(categorias)")
    columnas = cur.fetchall()

    # Ver todos los registros
    cur.execute("SELECT * FROM categorias")
    registros = cur.fetchall()

    conn.close()

    return {
        "columnas": [dict(row) for row in columnas],
        "registros": [dict(row) for row in registros]
    }
# ============================================================
#  CARGAR DATOS DE EJEMPLO (solo admin)
# ============================================================

@app.route("/admin/seed")
@login_required(role="admin")
def admin_seed():
    import sqlite3
    from datetime import date

    conn = get_db()
    cur = conn.cursor()

    # Evitar duplicados si ya existe algo
    cur.execute("SELECT COUNT(*) FROM misas")
    if cur.fetchone()[0] > 0:
        flash("⚠️ La base de datos ya contiene registros. No se insertó nada nuevo.")
        conn.close()
        return redirect("/admin")

    # === 1️⃣ Crear una misa ===
    hoy = date.today().isoformat()
    cur.execute("INSERT INTO misas (fecha, hora, ampm) VALUES (?, ?, ?)", (hoy, "11:00", "AM"))
    misa_id = cur.lastrowid

    # === 2️⃣ Crear categorías ===
    categorias = [
        ("ACCION DE GRACIAS", "ACCION DE GRACIAS", "EN ACCION DE GRACIAS POR:", 3),
        ("SALUD", "SALUD", "POR LA SALUD DE:", 2),
        ("DIFUNTOS", "DIFUNTOS", "POR EL ALIVIO Y ETERNO DESCANSO DE:", 1),
        ("VARIOS", "VARIOS", "VARIOS", 4),
        ("INTENCIONES", "INTENCIONES", "INTENCIONES Y NECESIDADES PERSONALES DE:", 5),
    ]
    cur.executemany("""
        INSERT INTO categorias (nombre, descripcion, texto_adicional, orden, active)
        VALUES (?, ?, ?, ?, 1)
    """, categorias)

    cur.execute("SELECT id, nombre FROM categorias")
    cat_map = {row[1]: row[0] for row in cur.fetchall()}

    # === 3️⃣ Frases base ===
    frases = [
        ("ESPIRITU SANTO",),
        ("NUESTRO SEÑOR",),
        ("SEÑOR DE LOS MILAGROS",),
        ("VIRGEN DE FATIMA",),
        ("SANTISIMA VIRGEN",)
    ]
    cur.executemany("INSERT INTO intencion_base (frase) VALUES (?)", frases)

    # === 4️⃣ Texto global (PDF) ===
    cur.execute("""
        INSERT INTO settings (key, value)
        VALUES ('pdf_texto_global', 'NOS PREPARAMOS PARA LA SANTA MISA. AGRADECEMOS A TODOS COLOCAR SUS TELEFONOS EN MODO SILENCIO. AVE MARIA PURISIMA...')
    """)

    # === 5️⃣ Intenciones ===
    intenciones = [
        # Acción de gracias
        (misa_id, cat_map["ACCION DE GRACIAS"], "FAVORES RECIBIDOS", "NUBIA CORTES Y FAMILIAS CHAVEZ RODRIGUEZ", None),
        (misa_id, cat_map["ACCION DE GRACIAS"], "FAVORES RECIBIDOS", "BURBANO CAMACHO Y GUSTAVO SANDOVAL", None),

        # Salud
        (misa_id, cat_map["SALUD"], "HELBERTH GOYENCHE", "", None),
        (misa_id, cat_map["SALUD"], "JULIO CESAR GOYENCHE", "", None),
        (misa_id, cat_map["SALUD"], "TOMAS CRUZ", "", None),
        (misa_id, cat_map["SALUD"], "GUSTAVO SANDOVAL", "", None),
        (misa_id, cat_map["SALUD"], "EMILIO RAMIREZ", "", None),
        (misa_id, cat_map["SALUD"], "MANUELA FORERO", "", None),
        (misa_id, cat_map["SALUD"], "IVANA LERSUNDY", "", None),
        (misa_id, cat_map["SALUD"], "ISAIAS FORERO", "", None),
        (misa_id, cat_map["SALUD"], "MARTIN MARULANDA", "", None),
        (misa_id, cat_map["SALUD"], "JOSE MIGUEL SERNA Y FAMILIA", "", None),
        (misa_id, cat_map["SALUD"], "CRISTIAN CUERVO", "", None),
        (misa_id, cat_map["SALUD"], "SANDRA VARGAS", "", None),
        (misa_id, cat_map["SALUD"], "ANA SILVIA PENAGOS", "", None),
        (misa_id, cat_map["SALUD"], "ANGELO Y FAMILIA", "", None),
        (misa_id, cat_map["SALUD"], "SAMUEL PRIMICIERO MURCIA", "", None),
        (misa_id, cat_map["SALUD"], "PRIMICIERO QUINTERO", "", None),
        (misa_id, cat_map["SALUD"], "CELY LOPEZ", "", None),
        (misa_id, cat_map["SALUD"], "CORCHUELO BELTRAN", "", None),
        (misa_id, cat_map["SALUD"], "ALBA PANCHE", "", None),
        (misa_id, cat_map["SALUD"], "RAUL PRIMICIERO", "", None),
        (misa_id, cat_map["SALUD"], "ANITA PRIMICIERO", "", None),
        (misa_id, cat_map["SALUD"], "MAGDA LILIANA RUIZ Y FAMILIAS PRIMICIERO MURCIA", "", None),
        (misa_id, cat_map["SALUD"], "CAMACHO ARAGON", "", None),
        (misa_id, cat_map["SALUD"], "BELTRAN OSORIO Y GOYENCHE GOYENCHE", "", None),
        
        # Difuntos
        (misa_id, cat_map["DIFUNTOS"], "ASTRID ROCIO PEREZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "BETTY ABIGAIL SIERRA", "", None),
        (misa_id, cat_map["DIFUNTOS"], "JOSE PRIMICIERO", "", None),
        (misa_id, cat_map["DIFUNTOS"], "AMINTA FAJARDO", "", None),
        (misa_id, cat_map["DIFUNTOS"], "LAURA MARIA MOLINA", "", None),
        (misa_id, cat_map["DIFUNTOS"], "MARIA DOLORES SANCHEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "BLANCA SANCHEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "CARLOS SANCHEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "MICGUEL SANCHEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "MELIDA ARDOL", "", None),
        (misa_id, cat_map["DIFUNTOS"], "LUIS SOLARTE Y NELLY DE SOLARTE", "", None),
        (misa_id, cat_map["DIFUNTOS"], "CARMEN ALVAREZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "JAIME GOMEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "ANTONIO NIÑO", "", None),
        (misa_id, cat_map["DIFUNTOS"], "LUIS SANDOVAL", "", None),
        (misa_id, cat_map["DIFUNTOS"], "JESUS SANDOVAL", "", None),
        (misa_id, cat_map["DIFUNTOS"], "CONCEPCION SANDOVAL", "", None),
        (misa_id, cat_map["DIFUNTOS"], "ADAN LOPEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "ALICIA LOPEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "LEONCIA PENAGOZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "GEDUAR LOPEZ REY", "", None),
        (misa_id, cat_map["DIFUNTOS"], "MARIA FEGENIA BARRERA", "", None),
        (misa_id, cat_map["DIFUNTOS"], "ETELMINA GOMEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "PARMENIO SANCHEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "JOHN BETANCURT", "", None),
        (misa_id, cat_map["DIFUNTOS"], "DIFUNTOS DE LA FAMILIA RUIZ GONZALEZ", "", None),
        (misa_id, cat_map["DIFUNTOS"], "RUIZ SARMIENTO Y BELTRAN OSORIO", "", None),

        # Varios
        (misa_id, cat_map["VARIOS"], "POR TODOS LOS PARTICIPANTES DE LA CAMPAÑA SALVADME REINA DE FATIMA", "", None),
        (misa_id, cat_map["VARIOS"], "POR TODOS LOS BENEFACTORES DE ESTA OBRA", "", None),
        (misa_id, cat_map["VARIOS"], "EN REPARACION POR LAS OFENSAS HECHAS AL SAGRADO CORAZON Y AL INMACULADO CORAZON DE MARIA", "FAMILIA RUIZ RUIZ", None),
        (misa_id, cat_map["VARIOS"], "POR EL TRABAJO DE EDGAR SIERRA", "", None),
        (misa_id, cat_map["VARIOS"], "POR EL TRABAJO DE MARILUZ CUERVO", "", None),
        (misa_id, cat_map["VARIOS"], "POR LA EMPRESA DOSIMETRIX", "", None),
        (misa_id, cat_map["VARIOS"], "POR EL TRABAJO DE MARILUZ CUERVO", "", None),

        # Intenciones
        (misa_id, cat_map["INTENCIONES"], "DORA LIZ PAEZ", "", None),
        (misa_id, cat_map["INTENCIONES"], "LILIANA CARDONA", "", None),
        (misa_id, cat_map["INTENCIONES"], "FAMILIAS RUIZ RUIZ", "", None),
        (misa_id, cat_map["INTENCIONES"], "LOPEX CELY", "", None),
        (misa_id, cat_map["INTENCIONES"], "CORCHUELO BELTRAN", "", None),
        (misa_id, cat_map["INTENCIONES"], "PATERNINA GUITERREZ", "", None),
        (misa_id, cat_map["INTENCIONES"], "POLO ORTEGA", "", None),
        (misa_id, cat_map["INTENCIONES"], "CARDONA RODRIGUEZ", "", None),
        (misa_id, cat_map["INTENCIONES"], "AGUILAR ASENSA", "", None),
    ]

    cur.executemany("""
        INSERT INTO intenciones (misa_id, categoria_id, peticiones, ofrece, intencion_base_id)
        VALUES (?, ?, ?, ?, ?)
    """, intenciones)

    conn.commit()
    conn.close()

    flash("✅ Datos de ejemplo cargados exitosamente.")
    return redirect("/admin")

    # ============================================================
    #  EJECUCIÓN LOCAL
    # ============================================================
    
    if __name__ == "__main__":
        app.run(host="0.0.0.0", port=5000)   
