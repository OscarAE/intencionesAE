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
    from reportlab.pdfgen import canvas
    import locale, io

    dia = request.form["dia"]
    conn = get_db()
    cur = conn.cursor()

    # Texto global guardado
    cur.execute("SELECT value FROM settings WHERE key='pdf_texto_global'")
    row = cur.fetchone()
    global_text = row["value"] if row else ""

    # Datos de misas del día
    cur.execute("SELECT * FROM misas WHERE fecha=? ORDER BY hora", (dia,))
    misas = cur.fetchall()

    # ======== CONFIG PDF ========
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    w, h = letter

    # ======== FUNCIONES AUXILIARES ========
    def dibujar_fondo(c):
        """Fondo completo y encabezado"""
        try:
            c.saveState()
            # Fondo
            c.drawImage("static/borde.png", 0, 0, width=w, height=h, mask="auto")
            # Título superior
            logo_width = 400
            logo_height = 65
            c.drawImage(
                "static/titulo.png",
                (w - logo_width) / 2,
                h - 110,
                width=logo_width,
                height=logo_height,
                mask="auto"
            )
            c.restoreState()
        except Exception as e:
            print("Error cargando imágenes:", e)

    # ======== FECHA EN ESPAÑOL ========
    try:
        locale.setlocale(locale.LC_TIME, "es_ES.utf8")
    except:
        try:
            locale.setlocale(locale.LC_TIME, "es_CO.utf8")
        except:
            locale.setlocale(locale.LC_TIME, "")

    dias = {
        "Monday": "LUNES", "Tuesday": "MARTES", "Wednesday": "MIÉRCOLES",
        "Thursday": "JUEVES", "Friday": "VIERNES", "Saturday": "SÁBADO", "Sunday": "DOMINGO"
    }
    meses = {
        "January": "ENERO", "February": "FEBRERO", "March": "MARZO", "April": "ABRIL",
        "May": "MAYO", "June": "JUNIO", "July": "JULIO", "August": "AGOSTO",
        "September": "SEPTIEMBRE", "October": "OCTUBRE", "November": "NOVIEMBRE", "December": "DICIEMBRE"
    }

    fecha_dt = datetime.strptime(dia, "%Y-%m-%d")
    dia_esp = dias[fecha_dt.strftime("%A")]
    mes_esp = meses[fecha_dt.strftime("%B")]
    fecha_formateada = f"{dia_esp} {fecha_dt.day} DE {mes_esp} DE {fecha_dt.year}"

    # ======== DIBUJAR FONDO Y ENCABEZADO ========
    dibujar_fondo(c)

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(w / 2, h - 130, f"INTENCIONES PARA LA SANTA MISA — {fecha_formateada}")
    y = h - 160

    # ======== CONTENIDO ========
    for misa in misas:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, f"MISA {misa['hora']} {misa['ampm']}")
        y -= 18

        cur.execute("""
            SELECT i.*, c.nombre AS cat, c.texto_adicional AS cat_text, b.frase AS base
            FROM intenciones i
            LEFT JOIN categorias c ON c.id=i.categoria_id
            LEFT JOIN intencion_base b ON b.id=i.intencion_base_id
            WHERE i.misa_id=?
            ORDER BY c.orden ASC
        """, (misa["id"],))
        items = cur.fetchall()

        print(f"🟢 MISA {misa['id']} → {len(items)} intenciones encontradas")
        for it in items:
            print(f"   - {it['cat']} | {it['peticiones']}")

        if not items:
            c.setFont("Helvetica", 10)
            c.drawString(70, y, "No hay intenciones registradas.")
            y -= 25
            continue

        cell_style = ParagraphStyle(name="CellStyle", fontName="Helvetica", fontSize=8, leading=10, spaceAfter=2)
        header_style = ParagraphStyle(name="HeaderStyle", fontName="Helvetica-Bold", fontSize=9, alignment=1, leading=11)

        # === Agrupar categorías respetando el orden definido en la BD ===
        categorias = []
        for it in items:
            cat = it["cat_text"] or it["cat"] or "SIN CATEGORÍA"
            if not categorias or categorias[-1][0] != cat:
                categorias.append((cat, [it]))
            else:
                categorias[-1][1].append(it)
        
        # === Iterar sobre la lista de categorías en el mismo orden SQL ===
        for cat_nombre, cat_items in categorias:
            nombre_upper = cat_nombre.strip().upper().replace("Ó", "O").replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ú", "U")
        
            c.setFont("Helvetica-Bold", 10)
            c.drawString(50, y, nombre_upper)
            y -= 15
        
            # === DIFUNTOS ===
            if nombre_upper.startswith("DIFUNT"):
                data = [[Paragraph("PETICIONES", header_style)] * 4]
                fila = []
                for it in cat_items:
                    pet = Paragraph(it["peticiones"] or "", cell_style)
                    fila.append(pet)
                    if len(fila) == 4:
                        data.append(fila)
                        fila = []
                if fila:
                    while len(fila) < 4:
                        fila.append(Paragraph("", cell_style))
                    data.append(fila)
        
                t = Table(data, colWidths=[110]*4)
                t.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('LINEABOVE', (0, 0), (-1, 0), 1.2, colors.black),
                    ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.black),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ]))
                w_table, h_table = t.wrapOn(c, w - 100, y)
                t.drawOn(c, 50, y - h_table)
                y -= h_table + 20
        
            # === SALUD ===
            elif "SALUD" in nombre_upper:
                peticiones = [it["peticiones"] for it in cat_items if it["peticiones"]]
                texto = ", ".join(peticiones)
                c.setFont("Helvetica", 8)
                for line in wrap(texto, 100):
                    c.drawString(60, y, line)
                    y -= 10
                y -= 10
        
            # === ACCION DE GRACIAS ===
            elif "GRACIAS" in nombre_upper:
                data = [[Paragraph("PETICIONES", header_style), Paragraph("OFRECE", header_style)]]
                for it in cat_items:
                    fila = [
                        Paragraph(it["peticiones"] or "", cell_style),
                        Paragraph(it["ofrece"] or "", cell_style)
                    ]
                    data.append(fila)
                t = Table(data, colWidths=[250, 250])
                t.setStyle(TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
                    ('LINEABOVE', (0, 0), (-1, 0), 1.2, colors.black),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ]))
                w_table, h_table = t.wrapOn(c, w - 100, y)
                t.drawOn(c, 50, y - h_table)
                y -= h_table + 20
        
            # === VARIOS ===
            elif "VARIOS" in nombre_upper:
                c.setFont("Helvetica", 8)
                for it in cat_items:
                    for line in wrap((it["peticiones"] or ""), 90):
                        c.drawString(60, y, f"- {line}")
                        y -= 10
                y -= 10
        
            # === OTRAS CATEGORÍAS (por defecto) ===
            else:
                c.setFont("Helvetica", 8)
                for it in cat_items:
                    texto = f"• {it['peticiones'] or ''}"
                    if "ofrece" in it.keys() and it["ofrece"]:
                        texto += f" — OFRECE: {it['ofrece']}"
                    for line in wrap(texto, 100):
                        c.drawString(60, y, line)
                        y -= 10
                    y -= 5
        
            # salto de página si se llena
            if y < 120:
                c.showPage()
                dibujar_fondo(c)
                y = h - 80

            # Nueva página si se llena
            if y < 120:
                c.showPage()
                dibujar_fondo(c)
                y = h - 80

    # ======== TEXTO GLOBAL ========
    global_text = request.form.get("texto_global", "").strip()
    if global_text:
        text_width = w - (5 * cm)
        wrapped_lines = wrap(" ".join(global_text.splitlines()), width=85)
        c.setFont("Helvetica-Bold", 9)
        for i, line in enumerate(wrapped_lines):
            y_line = (2 * cm) + 20 + (len(wrapped_lines) - i - 1) * 12
            c.drawCentredString(w / 2, y_line, line)

    # ======== PIE DE PÁGINA ========
    usuario = session["username"]
    now = datetime.now()
    dia_imp = dias[now.strftime("%A")]
    mes_imp = meses[now.strftime("%B")]
    hora_imp = now.strftime("%I:%M %p").upper()
    fecha_imp = f"{dia_imp} {now.day} DE {mes_imp} DE {now.year} A LAS {hora_imp}"

    c.setFont("Helvetica", 8)
    c.setFillGray(0.3)
    c.drawString(220, 55, f"IMPRESO POR: {usuario} — {fecha_imp}")
    c.save()

    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"intenciones_{dia}.pdf"
    )

# ============================================================
#  VERIFICA AL DB
# ============================================================
@app.route("/admin/debug_int_raw2")
@login_required(role="admin")
def debug_int_raw2():
    conn = get_db()
    cur = conn.cursor()

    # Ver las columnas
    cur.execute("PRAGMA table_info(settings)")
    columnas = cur.fetchall()

    # Ver todos los registros
    cur.execute("SELECT * FROM settings")
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
        (misa_id, cat_map["ACCION DE GRACIAS"], "Por la familia Pérez Rodríguez", "Familia Pérez", None),
        (misa_id, cat_map["ACCION DE GRACIAS"], "Por el cumpleaños de Ana María", "Su familia", None),

        # Salud
        (misa_id, cat_map["SALUD"], "Por la salud de Carlos Gómez", "", None),
        (misa_id, cat_map["SALUD"], "Por la pronta recuperación de Teresa", "", None),

        # Difuntos
        (misa_id, cat_map["DIFUNTOS"], "Por el eterno descanso de Luis García", "", None),
        (misa_id, cat_map["DIFUNTOS"], "Por las almas del purgatorio", "", None),

        # Varios
        (misa_id, cat_map["VARIOS"], "Por los jóvenes del grupo pastoral", "", None),
        (misa_id, cat_map["VARIOS"], "Por la conversión de los pecadores", "", None),

        # Intenciones
        (misa_id, cat_map["INTENCIONES"], "Por la paz del mundo", "", None),
        (misa_id, cat_map["INTENCIONES"], "Por el trabajo de los desempleados", "", None),
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
