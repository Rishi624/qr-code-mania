import os
import time
import uuid
from flask import Flask, render_template, request, send_file, url_for, flash
import qrcode
from io import BytesIO
import base64

app = Flask(__name__)
app.secret_key = 'codemania_secret'

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Database simulation
db = {}

@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    data_type = request.form.get('type')
    password = request.form.get('password')
    unique_id = str(uuid.uuid4())
    
    # Handle File or Text
    stored_data = ""
    if data_type == 'text':
        stored_data = request.form.get('text_content')
    else:
        if 'file_upload' not in request.files:
            return "No file found", 400
        file = request.files['file_upload']
        if file.filename == '':
            return "No selected file", 400
        filepath = os.path.join(UPLOAD_FOLDER, f"{unique_id}_{file.filename}")
        file.save(filepath)
        stored_data = filepath

    # Save to DB (10 mins expiry)
    db[unique_id] = {
        'type': data_type,
        'data': stored_data,
        'password': password if password.strip() != "" else None,
        'expiry': time.time() + 600
    }

    # Generate QR Code Image in Memory
    link = url_for('scan_qr', unique_id=unique_id, _external=True)
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert image to base64 string to show in HTML without saving to disk
    buf = BytesIO()
    img.save(buf)
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    
    return render_template('result.html', qr_image=img_b64, link=link)

@app.route('/scan/<unique_id>', methods=['GET', 'POST'])
def scan_qr(unique_id):
    entry = db.get(unique_id)
    
    if not entry or time.time() > entry['expiry']:
        return render_template('error.html', message="Link Expired or Invalid")

    # Password Logic
    if entry['password']:
        if request.method == 'POST':
            if request.form.get('pin') == entry['password']:
                 return deliver_content(entry)
            else:
                flash("Incorrect PIN", "error")
        return render_template('password.html', unique_id=unique_id)

    return deliver_content(entry)

def deliver_content(entry):
    if entry['type'] == 'text':
        return render_template('view_text.html', content=entry['data'])
    elif entry['type'] == 'file':
        return send_file(entry['data'], as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)