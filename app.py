import os
import json
import csv
import io
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "changeme-use-strong-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///botpanel.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ─── Models ───────────────────────────────────────────────────────────────────

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class BotUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    telegram_id = db.Column(db.String(50), unique=True, nullable=False)
    bot_name = db.Column(db.String(80), nullable=False)
    plan = db.Column(db.String(50), default="Basic")
    amount = db.Column(db.Integer, default=299)
    joined_date = db.Column(db.Date, default=date.today)
    notes = db.Column(db.Text, default="")
    payments = db.relationship("Payment", backref="user", lazy=True, cascade="all, delete-orphan")


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("bot_user.id"), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    paid = db.Column(db.Boolean, default=False)
    paid_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─── Auth helper ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ─── Init DB ──────────────────────────────────────────────────────────────────

def init_db():
    with app.app_context():
        db.create_all()
        if not Admin.query.first():
            admin = Admin(username="admin")
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            session["admin_id"] = admin.id
            return redirect(url_for("dashboard"))
        flash("Invalid username or password", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("admin_id", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    now = datetime.now()
    month, year = now.month, now.year
    total_users = BotUser.query.count()
    payments_this_month = Payment.query.filter_by(month=month, year=year).all()
    paid_count = sum(1 for p in payments_this_month if p.paid)
    pending_count = sum(1 for p in payments_this_month if not p.paid)
    revenue = sum(p.amount for p in payments_this_month if p.paid)
    pending_amount = sum(p.amount for p in payments_this_month if not p.paid)
    recent_payments = (
        Payment.query
        .filter_by(month=month, year=year)
        .join(BotUser)
        .order_by(Payment.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "dashboard.html",
        total_users=total_users,
        paid_count=paid_count,
        pending_count=pending_count,
        revenue=revenue,
        pending_amount=pending_amount,
        recent_payments=recent_payments,
        current_month=now.strftime("%B %Y"),
    )


@app.route("/users")
@login_required
def users():
    search = request.args.get("q", "").strip()
    bot_filter = request.args.get("bot", "").strip()
    query = BotUser.query
    if search:
        query = query.filter(
            db.or_(BotUser.name.ilike(f"%{search}%"), BotUser.telegram_id.ilike(f"%{search}%"))
        )
    if bot_filter:
        query = query.filter(BotUser.bot_name == bot_filter)
    all_users = query.order_by(BotUser.joined_date.desc()).all()
    all_bots = [r[0] for r in db.session.query(BotUser.bot_name).distinct().all()]
    now = datetime.now()
    # Attach current month payment status to each user
    for u in all_users:
        pay = Payment.query.filter_by(user_id=u.id, month=now.month, year=now.year).first()
        u.current_payment = pay
    return render_template("users.html", users=all_users, all_bots=all_bots, search=search, bot_filter=bot_filter)


@app.route("/users/add", methods=["GET", "POST"])
@login_required
def add_user():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        telegram_id = request.form.get("telegram_id", "").strip()
        bot_name = request.form.get("bot_name", "").strip()
        plan = request.form.get("plan", "Basic").strip()
        amount = int(request.form.get("amount", 299))
        notes = request.form.get("notes", "").strip()
        if BotUser.query.filter_by(telegram_id=telegram_id).first():
            flash("Telegram ID already exists!", "error")
            return redirect(url_for("add_user"))
        user = BotUser(name=name, telegram_id=telegram_id, bot_name=bot_name, plan=plan, amount=amount, notes=notes)
        db.session.add(user)
        db.session.flush()
        now = datetime.now()
        payment = Payment(user_id=user.id, month=now.month, year=now.year, amount=amount, paid=False)
        db.session.add(payment)
        db.session.commit()
        flash(f"User {name} added successfully!", "success")
        return redirect(url_for("users"))
    return render_template("add_user.html")


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def edit_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    if request.method == "POST":
        user.name = request.form.get("name", user.name).strip()
        user.bot_name = request.form.get("bot_name", user.bot_name).strip()
        user.plan = request.form.get("plan", user.plan).strip()
        user.amount = int(request.form.get("amount", user.amount))
        user.notes = request.form.get("notes", "").strip()
        db.session.commit()
        flash("User updated!", "success")
        return redirect(url_for("users"))
    return render_template("edit_user.html", user=user)


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
def delete_user(user_id):
    user = BotUser.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash(f"User {user.name} deleted.", "success")
    return redirect(url_for("users"))


@app.route("/payments")
@login_required
def payments():
    now = datetime.now()
    month = int(request.args.get("month", now.month))
    year = int(request.args.get("year", now.year))
    all_payments = (
        Payment.query
        .filter_by(month=month, year=year)
        .join(BotUser)
        .order_by(BotUser.name)
        .all()
    )
    paid_total = sum(p.amount for p in all_payments if p.paid)
    pending_total = sum(p.amount for p in all_payments if not p.paid)
    months_list = [(m, datetime(2025, m, 1).strftime("%B")) for m in range(1, 13)]
    return render_template(
        "payments.html",
        payments=all_payments,
        paid_total=paid_total,
        pending_total=pending_total,
        sel_month=month,
        sel_year=year,
        months_list=months_list,
        current_month=datetime(year, month, 1).strftime("%B %Y"),
    )


@app.route("/payments/mark/<int:payment_id>", methods=["POST"])
@login_required
def mark_paid(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    payment.paid = True
    payment.paid_date = date.today()
    db.session.commit()
    flash(f"{payment.user.name} marked as paid!", "success")
    return redirect(request.referrer or url_for("payments"))


@app.route("/payments/unmark/<int:payment_id>", methods=["POST"])
@login_required
def mark_unpaid(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    payment.paid = False
    payment.paid_date = None
    db.session.commit()
    flash(f"{payment.user.name} marked as unpaid.", "success")
    return redirect(request.referrer or url_for("payments"))


@app.route("/payments/generate", methods=["POST"])
@login_required
def generate_payments():
    now = datetime.now()
    month = int(request.form.get("month", now.month))
    year = int(request.form.get("year", now.year))
    users = BotUser.query.all()
    created = 0
    for user in users:
        exists = Payment.query.filter_by(user_id=user.id, month=month, year=year).first()
        if not exists:
            pay = Payment(user_id=user.id, month=month, year=year, amount=user.amount, paid=False)
            db.session.add(pay)
            created += 1
    db.session.commit()
    flash(f"Generated {created} payment records for {datetime(year, month, 1).strftime('%B %Y')}.", "success")
    return redirect(url_for("payments", month=month, year=year))


@app.route("/monthly")
@login_required
def monthly():
    now = datetime.now()
    months_data = []
    for m in range(1, 13):
        pays = Payment.query.filter_by(month=m, year=now.year).all()
        months_data.append({
            "month": datetime(now.year, m, 1).strftime("%B"),
            "month_num": m,
            "year": now.year,
            "total": len(pays),
            "paid": sum(1 for p in pays if p.paid),
            "pending": sum(1 for p in pays if not p.paid),
            "revenue": sum(p.amount for p in pays if p.paid),
            "pending_amt": sum(p.amount for p in pays if not p.paid),
        })
    return render_template("monthly.html", months_data=months_data, current_year=now.year)


@app.route("/export/csv")
@login_required
def export_csv():
    month = int(request.args.get("month", datetime.now().month))
    year = int(request.args.get("year", datetime.now().year))
    payments = Payment.query.filter_by(month=month, year=year).join(BotUser).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Telegram ID", "Bot", "Plan", "Amount", "Status", "Paid Date", "Month"])
    for p in payments:
        writer.writerow([
            p.user.name, p.user.telegram_id, p.user.bot_name,
            p.user.plan, p.amount,
            "Paid" if p.paid else "Pending",
            p.paid_date.strftime("%Y-%m-%d") if p.paid_date else "",
            f"{datetime(year, month, 1).strftime('%B %Y')}",
        ])
    output.seek(0)
    filename = f"botpanel_{datetime(year, month, 1).strftime('%B_%Y')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/stats")
@login_required
def api_stats():
    now = datetime.now()
    monthly = []
    for m in range(1, 13):
        pays = Payment.query.filter_by(month=m, year=now.year).all()
        monthly.append(sum(p.amount for p in pays if p.paid))
    return jsonify({"monthly_revenue": monthly, "year": now.year})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
