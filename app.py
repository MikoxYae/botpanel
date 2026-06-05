import os
import csv
import io
import logging
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
app.secret_key = os.environ.get("SECRET_KEY", "botpanel-fixed-secret-key-2025")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("BOTPANEL_DB_URL", "sqlite:///botpanel.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

db = SQLAlchemy(app)

# ─── Activity log file ────────────────────────────────────────────────────────
LOG_FILE = os.environ.get("LOG_FILE", "/opt/botpanel/logs/activity.log")
try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
except Exception:
    LOG_FILE = "/tmp/botpanel_activity.log"

def _log(msg: str):
    """Append a timestamped line to the activity log."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ─── Models ───────────────────────────────────────────────────────────────────

class Admin(db.Model):
    __tablename__ = "admin"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class BotUser(db.Model):
    __tablename__ = "bot_user"
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), nullable=False)
    telegram_id = db.Column(db.String(50), unique=True, nullable=False)
    bot_name    = db.Column(db.String(80), nullable=False)
    plan        = db.Column(db.String(50), default="Basic")
    amount      = db.Column(db.Integer, default=299)
    start_date  = db.Column(db.Date, nullable=True)
    joined_date = db.Column(db.Date, default=date.today)
    notes       = db.Column(db.Text, default="")
    deleted_at  = db.Column(db.DateTime, nullable=True)
    payments    = db.relationship("Payment", backref="user", lazy=True, cascade="all, delete-orphan")


class Payment(db.Model):
    __tablename__ = "payment"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("bot_user.id"), nullable=False)
    month      = db.Column(db.Integer, nullable=False)
    year       = db.Column(db.Integer, nullable=False)
    amount     = db.Column(db.Integer, nullable=False)
    paid       = db.Column(db.Boolean, default=False)
    paid_date  = db.Column(db.Date, nullable=True)
    note       = db.Column(db.Text, default="")          # per-month note
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class QuickLink(db.Model):
    __tablename__ = "quick_link"
    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(80), nullable=False)
    url        = db.Column(db.String(500), nullable=False)
    icon       = db.Column(db.String(10), default="🔗")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ─── DB Init + Migration ──────────────────────────────────────────────────────

def init_db():
    with app.app_context():
        db.create_all()
        for stmt in [
            "ALTER TABLE bot_user ADD COLUMN IF NOT EXISTS start_date DATE",
            "ALTER TABLE bot_user ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP",
            "ALTER TABLE payment ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''",
        ]:
            try:
                db.session.execute(db.text(stmt))
                db.session.commit()
            except Exception:
                db.session.rollback()
        if not Admin.query.first():
            admin = Admin(username="admin")
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
        _log("Server started / DB initialised")


# ─── Context processor ────────────────────────────────────────────────────────

@app.context_processor
def inject_quick_links():
    try:
        links = QuickLink.query.order_by(QuickLink.created_at).all()
    except Exception:
        links = []
    return dict(quick_links=links)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if "admin_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            session.permanent = True
            session["admin_id"] = admin.id
            _log(f"LOGIN  admin={username}  ip={request.remote_addr}")
            return redirect(url_for("dashboard"))
        _log(f"LOGIN_FAIL  username={username}  ip={request.remote_addr}")
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    _log(f"LOGOUT  admin_id={session.get('admin_id')}")
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    now = datetime.now()
    month, year = now.month, now.year
    total_users = BotUser.query.filter_by(deleted_at=None).count()
    payments_this_month = (Payment.query.filter_by(month=month, year=year)
                           .join(BotUser).filter(BotUser.deleted_at == None).all())
    paid_count    = sum(1 for p in payments_this_month if p.paid)
    pending_count = sum(1 for p in payments_this_month if not p.paid)
    revenue       = sum(p.amount for p in payments_this_month if p.paid)
    recent_payments = (
        Payment.query.filter_by(month=month, year=year)
        .join(BotUser).filter(BotUser.deleted_at == None)
        .order_by(Payment.created_at.desc()).limit(10).all()
    )
    return render_template(
        "dashboard.html",
        total_users=total_users, paid_count=paid_count,
        pending_count=pending_count, revenue=revenue,
        recent_payments=recent_payments,
        current_month=now.strftime("%B %Y"),
    )


@app.route("/users")
@login_required
def users():
    search     = request.args.get("q", "").strip()
    bot_filter = request.args.get("bot", "").strip()
    now        = datetime.now()
    sel_month  = int(request.args.get("month", now.month))
    sel_year   = int(request.args.get("year",  now.year))

    query = BotUser.query.filter_by(deleted_at=None)
    if search:
        query = query.filter(db.or_(
            BotUser.name.ilike(f"%{search}%"),
            BotUser.telegram_id.ilike(f"%{search}%")))
    if bot_filter:
        query = query.filter(BotUser.bot_name == bot_filter)
    all_users = query.order_by(BotUser.joined_date.desc()).all()
    all_bots  = [r[0] for r in db.session.query(BotUser.bot_name)
                 .filter(BotUser.deleted_at == None).distinct().all()]
    today = date.today()

    sel_date = date(sel_year, sel_month, 1)
    auto_created = 0
    for u in all_users:
        user_start = u.start_date or u.joined_date
        # Only create payment records for months on/after the user's start date
        if user_start and sel_date >= date(user_start.year, user_start.month, 1):
            if not Payment.query.filter_by(user_id=u.id, month=sel_month, year=sel_year).first():
                db.session.add(Payment(user_id=u.id, month=sel_month, year=sel_year,
                                       amount=u.amount, paid=False))
                auto_created += 1
    if auto_created:
        db.session.commit()

    for u in all_users:
        u.current_payment = Payment.query.filter_by(
            user_id=u.id, month=sel_month, year=sel_year).first()
        _ref = u.start_date or u.joined_date
        u.months_running = (today - _ref).days // 30 if _ref else 0
        u.before_start = bool(_ref and sel_date < date(_ref.year, _ref.month, 1))

    months_list = [(m, datetime(sel_year, m, 1).strftime("%B")) for m in range(1, 13)]
    return render_template(
        "users.html",
        users=all_users, all_bots=all_bots,
        search=search, bot_filter=bot_filter,
        sel_month=sel_month, sel_year=sel_year,
        months_list=months_list,
    )


@app.route("/users/add", methods=["GET", "POST"])
@login_required
def add_user():
    if request.method == "POST":
        name           = request.form.get("name", "").strip()
        telegram_id    = request.form.get("telegram_id", "").strip()
        bot_name       = request.form.get("bot_name", "").strip()
        plan           = request.form.get("plan", "Basic").strip()
        amount         = int(request.form.get("amount", 299))
        notes          = request.form.get("notes", "").strip()
        start_date_str = request.form.get("start_date", "").strip()
        start_date     = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else None

        if BotUser.query.filter(BotUser.telegram_id == telegram_id, BotUser.deleted_at == None).first():
            flash("Telegram ID already exists!", "error")
            return redirect(url_for("add_user"))

        # If a soft-deleted record exists with same telegram_id, purge it so the
        # DB unique constraint doesn't block the new INSERT.
        old = BotUser.query.filter(BotUser.telegram_id == telegram_id, BotUser.deleted_at != None).first()
        if old:
            db.session.delete(old)
            db.session.flush()

        user = BotUser(name=name, telegram_id=telegram_id, bot_name=bot_name,
                       plan=plan, amount=amount, notes=notes, start_date=start_date)
        db.session.add(user)
        db.session.flush()

        now = datetime.now()
        if start_date:
            cur = date(start_date.year, start_date.month, 1)
            end = date(now.year, now.month, 1)
            while cur <= end:
                db.session.add(Payment(user_id=user.id, month=cur.month, year=cur.year,
                                       amount=amount, paid=False))
                cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
        else:
            db.session.add(Payment(user_id=user.id, month=now.month, year=now.year,
                                   amount=amount, paid=False))

        db.session.commit()
        _log(f"ADD_USER  name={name}  tg={telegram_id}  bot={bot_name}  plan={plan}  amt={amount}")
        flash(f"User {name} added!", "success")
        return redirect(url_for("users"))
    return render_template("add_user.html", today=date.today().isoformat())


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    if request.method == "POST":
        user.name     = request.form.get("name", user.name).strip()
        user.bot_name = request.form.get("bot_name", user.bot_name).strip()
        user.plan     = request.form.get("plan", user.plan).strip()
        user.amount   = int(request.form.get("amount", user.amount))
        user.notes    = request.form.get("notes", "").strip()
        sd = request.form.get("start_date", "").strip()
        user.start_date = datetime.strptime(sd, "%Y-%m-%d").date() if sd else user.start_date
        db.session.commit()
        _log(f"EDIT_USER  id={user_id}  name={user.name}")
        flash("User updated!", "success")
        return redirect(url_for("users"))
    return render_template("edit_user.html", user=user)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    user.deleted_at = datetime.utcnow()
    db.session.commit()
    _log(f"DELETE_USER  id={user_id}  name={user.name}")
    flash(f"{user.name} moved to Trash. <a href='{url_for('trash')}'>View Trash</a>", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/backfill", methods=["POST"])
@login_required
def backfill_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    if not user.start_date:
        flash("Start date set nahi hai — pehle edit kar ke start date daalo.", "error")
        return redirect(url_for("edit_user", user_id=user_id))
    now = datetime.now()
    cur = date(user.start_date.year, user.start_date.month, 1)
    end = date(now.year, now.month, 1)
    created = 0
    while cur <= end:
        if not Payment.query.filter_by(user_id=user.id, month=cur.month, year=cur.year).first():
            db.session.add(Payment(user_id=user.id, month=cur.month, year=cur.year,
                                   amount=user.amount, paid=False))
            created += 1
        cur = date(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
    db.session.commit()
    _log(f"BACKFILL  user={user.name}  created={created}")
    flash(f"{user.name}: {created} missing records backfilled from {user.start_date.strftime('%b %Y')}.", "success")
    return redirect(url_for("users"))


@app.route("/trash")
@login_required
def trash():
    deleted = BotUser.query.filter(BotUser.deleted_at != None).order_by(BotUser.deleted_at.desc()).all()
    return render_template("trash.html", deleted_users=deleted)


@app.route("/users/<int:user_id>/restore", methods=["POST"])
@login_required
def restore_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    user.deleted_at = None
    db.session.commit()
    _log(f"RESTORE_USER  id={user_id}  name={user.name}")
    flash(f"{user.name} restored!", "success")
    return redirect(url_for("trash"))


@app.route("/users/<int:user_id>/purge", methods=["POST"])
@login_required
def purge_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    name = user.name
    db.session.delete(user)
    db.session.commit()
    _log(f"PURGE_USER  id={user_id}  name={name}")
    flash(f"{name} permanently deleted.", "success")
    return redirect(url_for("trash"))


@app.route("/payments")
@login_required
def payments():
    now   = datetime.now()
    month = int(request.args.get("month", now.month))
    year  = int(request.args.get("year", now.year))

    auto_created = 0
    for user in BotUser.query.filter_by(deleted_at=None).all():
        start = user.start_date or user.joined_date
        if start and date(year, month, 1) >= date(start.year, start.month, 1):
            if not Payment.query.filter_by(user_id=user.id, month=month, year=year).first():
                db.session.add(Payment(user_id=user.id, month=month, year=year,
                                       amount=user.amount, paid=False))
                auto_created += 1
    if auto_created:
        db.session.commit()

    all_payments = (
        Payment.query.filter_by(month=month, year=year)
        .join(BotUser).filter(BotUser.deleted_at == None)
        .order_by(BotUser.name).all()
    )
    paid_total    = sum(p.amount for p in all_payments if p.paid)
    pending_total = sum(p.amount for p in all_payments if not p.paid)
    months_list   = [(m, datetime(year, m, 1).strftime("%B")) for m in range(1, 13)]
    years_list    = list(range(now.year - 2, now.year + 2))
    return render_template(
        "payments.html",
        payments=all_payments, paid_total=paid_total, pending_total=pending_total,
        sel_month=month, sel_year=year,
        months_list=months_list, years_list=years_list,
        current_month=datetime(year, month, 1).strftime("%B %Y"),
    )


@app.route("/payments/mark/<int:payment_id>", methods=["POST"])
@login_required
def mark_paid(payment_id):
    p = Payment.query.get_or_404(payment_id)
    p.paid = True
    p.paid_date = date.today()
    db.session.commit()
    _log(f"PAYMENT_PAID  user={p.user.name}  month={p.month}/{p.year}  amt={p.amount}")
    flash(f"{p.user.name} marked as paid!", "success")
    return redirect(request.referrer or url_for("payments"))


@app.route("/payments/unmark/<int:payment_id>", methods=["POST"])
@login_required
def mark_unpaid(payment_id):
    p = Payment.query.get_or_404(payment_id)
    p.paid = False
    p.paid_date = None
    db.session.commit()
    _log(f"PAYMENT_UNPAID  user={p.user.name}  month={p.month}/{p.year}")
    flash(f"{p.user.name} marked as unpaid.", "success")
    return redirect(request.referrer or url_for("payments"))


@app.route("/payments/note/<int:payment_id>", methods=["POST"])
@login_required
def update_payment_note(payment_id):
    p = Payment.query.get_or_404(payment_id)
    p.note = request.form.get("note", "").strip()
    db.session.commit()
    return redirect(request.referrer or url_for("payments"))


@app.route("/payments/delete/<int:payment_id>", methods=["POST"])
@login_required
def delete_payment(payment_id):
    p = Payment.query.get_or_404(payment_id)
    name = p.user.name
    db.session.delete(p)
    db.session.commit()
    _log(f"DELETE_PAYMENT  user={name}  month={p.month}/{p.year}")
    flash(f"Payment record for {name} deleted.", "success")
    return redirect(request.referrer or url_for("payments"))


@app.route("/payments/generate", methods=["POST"])
@login_required
def generate_payments():
    now    = datetime.now()
    month  = int(request.form.get("month", now.month))
    year   = int(request.form.get("year", now.year))
    created = 0
    for user in BotUser.query.filter_by(deleted_at=None).all():
        if not Payment.query.filter_by(user_id=user.id, month=month, year=year).first():
            db.session.add(Payment(user_id=user.id, month=month, year=year,
                                   amount=user.amount, paid=False))
            created += 1
    db.session.commit()
    flash(f"Generated {created} new records for {datetime(year, month, 1).strftime('%B %Y')}.", "success")
    return redirect(url_for("payments", month=month, year=year))


@app.route("/monthly")
@login_required
def monthly():
    now = datetime.now()
    months_data = []
    for m in range(1, 13):
        pays = (Payment.query.filter_by(month=m, year=now.year)
                .join(BotUser).filter(BotUser.deleted_at == None).all())
        months_data.append({
            "month":       datetime(now.year, m, 1).strftime("%B"),
            "month_num":   m,
            "year":        now.year,
            "total":       len(pays),
            "paid":        sum(1 for p in pays if p.paid),
            "pending":     sum(1 for p in pays if not p.paid),
            "revenue":     sum(p.amount for p in pays if p.paid),
            "pending_amt": sum(p.amount for p in pays if not p.paid),
        })
    return render_template("monthly.html", months_data=months_data, current_year=now.year)


@app.route("/export/csv")
@login_required
def export_csv():
    month  = int(request.args.get("month", datetime.now().month))
    year   = int(request.args.get("year",  datetime.now().year))
    pays   = (Payment.query.filter_by(month=month, year=year)
              .join(BotUser).filter(BotUser.deleted_at == None).all())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name","Telegram ID","Bot","Plan","Amount","Status","Paid Date","Start Date","Note","Month"])
    for p in pays:
        writer.writerow([
            p.user.name, p.user.telegram_id, p.user.bot_name, p.user.plan, p.amount,
            "Paid" if p.paid else "Pending",
            p.paid_date.strftime("%Y-%m-%d") if p.paid_date else "",
            p.user.start_date.strftime("%Y-%m-%d") if p.user.start_date else "",
            p.note or "",
            datetime(year, month, 1).strftime("%B %Y"),
        ])
    output.seek(0)
    filename = f"yaemiko_{datetime(year, month, 1).strftime('%B_%Y')}.csv"
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ─── Settings / Quick Links ───────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_link":
            title = request.form.get("title", "").strip()
            url_  = request.form.get("url", "").strip()
            icon  = request.form.get("icon", "🔗").strip() or "🔗"
            if title and url_:
                if not url_.startswith("http"):
                    url_ = "https://" + url_
                db.session.add(QuickLink(title=title, url=url_, icon=icon))
                db.session.commit()
                _log(f"ADD_LINK  title={title}  url={url_}")
                flash(f'Button "{title}" added!', "success")
        elif action == "delete_link":
            link = QuickLink.query.get(int(request.form.get("link_id", 0)))
            if link:
                db.session.delete(link)
                db.session.commit()
                flash("Button deleted.", "success")
        elif action == "change_password":
            old_pw = request.form.get("old_password", "")
            new_pw = request.form.get("new_password", "")
            admin  = Admin.query.get(session["admin_id"])
            if admin and admin.check_password(old_pw) and len(new_pw) >= 6:
                admin.set_password(new_pw)
                db.session.commit()
                _log("PASSWORD_CHANGED")
                flash("Password changed!", "success")
            else:
                flash("Wrong current password or new password too short (min 6).", "error")
        return redirect(url_for("settings"))
    links = QuickLink.query.order_by(QuickLink.created_at).all()
    return render_template("settings.html", links=links)


# ─── Logs ─────────────────────────────────────────────────────────────────────

@app.route("/logs")
@login_required
def logs():
    return render_template("logs.html")


@app.route("/logs/data")
@login_required
def logs_data():
    lines = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            raw = f.readlines()
        lines = [l.rstrip() for l in raw[-500:]]   # last 500 lines
    except FileNotFoundError:
        lines = ["[No log file yet — actions will appear here as you use the panel]"]
    except Exception as e:
        lines = [f"[Error reading log: {e}]"]
    return jsonify({"lines": lines, "file": LOG_FILE})


@app.route("/logs/clear", methods=["POST"])
@login_required
def logs_clear():
    try:
        open(LOG_FILE, "w").close()
        _log("LOGS_CLEARED")
        flash("Logs cleared.", "success")
    except Exception as e:
        flash(f"Could not clear log: {e}", "error")
    return redirect(url_for("logs"))


# ─── API stats ────────────────────────────────────────────────────────────────

@app.route("/api/stats")
@login_required
def api_stats():
    now     = datetime.now()
    monthly = []
    for m in range(1, 13):
        pays = (Payment.query.filter_by(month=m, year=now.year)
                .join(BotUser).filter(BotUser.deleted_at == None).all())
        monthly.append(sum(p.amount for p in pays if p.paid))
    return jsonify({"monthly_revenue": monthly, "year": now.year})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
