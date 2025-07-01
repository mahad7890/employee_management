from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, make_response
from flask_mysqldb import MySQL
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date
from io import StringIO
import os
import csv
import qrcode

app = Flask(__name__, static_folder='static')
app.secret_key = 'secretkey'
app.config['UPLOAD_FOLDER'] = 'uploads'

app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'employee_db'

mysql = MySQL(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Make necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/qrcodes', exist_ok=True)

class User(UserMixin):
    def __init__(self, id_, username):
        self.id = id_
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = %s", [user_id])
    user = cur.fetchone()
    return User(user[0], user[1]) if user else None

def is_admin():
    return current_user and current_user.id and str(current_user.id).isdigit() == False

@app.route('/')
@login_required
def index():
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM employees")
    data = cur.fetchall()
    return render_template('index.html', employees=data)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cur = mysql.connection.cursor()

        cur.execute("SELECT * FROM users WHERE username = %s", [username])
        user = cur.fetchone()
        if user and check_password_hash(user[2], password):
            login_user(User(user[0], user[1]))
            return redirect('/')

        flash("Invalid credentials")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if request.method == 'POST':
        data = request.form
        photo = request.files['photo']
        filename = secure_filename(photo.filename)
        photo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO employees (name, username, email, password, city, photo) VALUES (%s,%s,%s,%s,%s,%s)",
                    (data['name'], data['username'], data['email'], data['password'], data['city'], filename))
        mysql.connection.commit()
        emp_id = cur.lastrowid

        try:
            qr = qrcode.make(str(emp_id))
            qr_dir = os.path.join(app.static_folder, 'qrcodes')
            os.makedirs(qr_dir, exist_ok=True)
            qr_path = os.path.join(qr_dir, f"{emp_id}.png")
            qr.save(qr_path)
        except Exception as e:
            print("QR generation failed:", e)

        flash('Employee added successfully')
        return redirect('/')
    return render_template('add_edit.html', action='Add', emp=None)

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit(id):
    cur = mysql.connection.cursor()
    if request.method == 'POST':
        data = request.form
        cur.execute("UPDATE employees SET name=%s, username=%s, email=%s, password=%s, city=%s WHERE id=%s",
                    (data['name'], data['username'], data['email'], data['password'], data['city'], id))
        mysql.connection.commit()
        flash('Employee updated')
        return redirect('/')
    cur.execute("SELECT * FROM employees WHERE id=%s", [id])
    emp = cur.fetchone()
    return render_template('add_edit.html', action='Edit', emp=emp)

@app.route('/delete/<int:id>')
@login_required
def delete(id):
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM employees WHERE id=%s", [id])
    mysql.connection.commit()
    flash('Employee deleted')
    return redirect('/')

@app.route('/scan')
@login_required
def scan_qr():
    return render_template('scan.html')

@app.route('/mark_attendance', methods=['POST'])
@login_required
def mark_attendance():
    data = request.get_json()
    emp_id = data['employee_id']
    today = date.today()

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM attendance WHERE employee_id=%s AND date=%s", (emp_id, today))
    record = cur.fetchone()
    now = datetime.now().time()

    cur.execute("SELECT name FROM employees WHERE id=%s", [emp_id])
    emp = cur.fetchone()
    emp_name = emp[0] if emp else 'Employee'

    if not record:
        cur.execute("INSERT INTO attendance (employee_id, date, sign_in) VALUES (%s, %s, %s)",
                    (emp_id, today, now))
        mysql.connection.commit()
        return jsonify({'message': f'{emp_name} - Sign In recorded'})
    elif record[3] is None:
        cur.execute("UPDATE attendance SET sign_out=%s WHERE id=%s", (now, record[0]))
        mysql.connection.commit()
        return jsonify({'message': f'{emp_name} - Sign Out recorded'})
    else:
        return jsonify({'message': f'{emp_name} - Already Signed Out'})

@app.route('/dashboard')
@login_required
def dashboard():
    if not is_admin():
        return redirect('/')

    today = date.today()
    cur = mysql.connection.cursor()
    cur.execute("SELECT COUNT(*) FROM employees")
    total_employees = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM attendance WHERE date=%s", [today])
    present = cur.fetchone()[0]
    absent = total_employees - present

    cur.execute("""
        SELECT e.name, a.date, a.sign_in, a.sign_out 
        FROM attendance a 
        JOIN employees e ON e.id = a.employee_id 
        ORDER BY a.date DESC
    """)
    records = cur.fetchall()

    cur.execute("SELECT date, COUNT(*) FROM attendance GROUP BY date ORDER BY date ASC LIMIT 10")
    trend_data = cur.fetchall()
    labels = [str(r[0]) for r in trend_data]
    values = [r[1] for r in trend_data]

    cur.execute("SELECT * FROM employees")
    employees = cur.fetchall()

    return render_template('dashboard.html', present=present, absent=absent, total=total_employees,
                           records=records, labels=labels, values=values, employees=employees)

@app.route('/export_attendance')
@login_required
def export_attendance():
    if not is_admin():
        return redirect('/')

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT e.name, a.date, a.sign_in, a.sign_out 
        FROM attendance a 
        JOIN employees e ON e.id = a.employee_id
        ORDER BY a.date DESC
    """)
    data = cur.fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Name', 'Date', 'Sign In', 'Sign Out'])
    cw.writerows(data)

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=attendance.csv"
    output.headers["Content-type"] = "text/csv"
    return output

if __name__ == '__main__':
    app.run(debug=True)
