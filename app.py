import os
import time
import uuid
import json
from flask import Flask, render_template, request, send_file, url_for, jsonify
import qrcode
from PIL import Image, ImageEnhance, ImageOps
from io import BytesIO
import base64
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'codemania_final_v5')

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'uploads'
DATA_FOLDER = 'data'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)

# --- HELPER FUNCTIONS ---
def save_entry(unique_id, data):
    with open(os.path.join(DATA_FOLDER, f"{unique_id}.json"), 'w') as f:
        json.dump(data, f)

def load_entry(unique_id):
    path = os.path.join(DATA_FOLDER, f"{unique_id}.json")
    if not os.path.exists(path): return None
    try:
        with open(path, 'r') as f: return json.load(f)
    except: return None

# --- ROUTES ---
@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate():
    # 1. Basic Data
    data_type = request.form.get('type')
    password = request.form.get('password')
    max_scans = request.form.get('max_scans')
    max_scans = int(max_scans) if max_scans and max_scans.strip() else 100
    qr_color_hex = request.form.get('color', '#000000')

    # 2. Design Data
    stickers_json = request.form.get('stickers_data')
    background_data = request.form.get('background_data') 
    
    unique_id = str(uuid.uuid4())
    stored_data = ""

    # 3. Handle Content
    if data_type == 'text':
        stored_data = request.form.get('text_content')
    elif data_type == 'file':
        if 'file_upload' in request.files:
            file = request.files['file_upload']
            if file.filename:
                ext = os.path.splitext(file.filename)[1]
                filepath = os.path.join(UPLOAD_FOLDER, f"{unique_id}{ext}")
                file.save(filepath)
                stored_data = filepath
    elif data_type == 'audio':
        if 'audio_blob' in request.files:
            file = request.files['audio_blob']
            # We use .webm because browsers record in webm natively
            filepath = os.path.join(UPLOAD_FOLDER, f"{unique_id}.webm")
            file.save(filepath)
            stored_data = filepath

    # 4. Save DB
    entry = {
        'id': unique_id,
        'type': data_type,
        'data': stored_data,
        'password': password if password and password.strip() else None,
        'expiry': time.time() + 600,
        'max_scans': max_scans,
        'current_scans': 0
    }
    save_entry(unique_id, entry)

    # 5. Generate QR Code (Standard Black/White first)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=2)
    link = url_for('scan_qr', unique_id=unique_id, _external=True)
    qr.add_data(link)
    qr.make(fit=True)

    # Convert to RGBA immediately to support transparency
    qr_img = qr.make_image().convert("RGBA")
    
    # 6. Manual Recolor (Prevents IndexError)
    # We replace black pixels with the user's chosen color
    # We replace white pixels with transparent if background is set, else white
    
    datas = qr_img.getdata()
    new_data = []
    
    # User Color (Target)
    h = qr_color_hex.lstrip('#')
    user_rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    
    has_bg = True if (background_data and len(background_data) > 10) else False

    for item in datas:
        # item is (R, G, B, A). Black is (0,0,0,255), White is (255,255,255,255)
        if item[0] < 128:  # It's a dark pixel (The QR Data)
            new_data.append((user_rgb[0], user_rgb[1], user_rgb[2], 255))
        else:  # It's a white pixel (The Background)
            if has_bg:
                new_data.append((255, 255, 255, 0)) # Transparent
            else:
                new_data.append((255, 255, 255, 255)) # White

    qr_img.putdata(new_data)
    final_img = qr_img
    qr_w, qr_h = qr_img.size

    # 7. Apply Background Layer
    if has_bg:
        try:
            bg_data = json.loads(background_data)
            if bg_data and 'src' in bg_data:
                bg_src = bg_data['src']
                bg_img = None
                
                if bg_src.startswith('data:image'):
                    head, data = bg_src.split(',', 1)
                    bg_img = Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")
                elif bg_src.startswith('http'):
                    resp = requests.get(bg_src)
                    bg_img = Image.open(BytesIO(resp.content)).convert("RGBA")
                
                if bg_img:
                    # Resize background to cover the QR area
                    bg_img = bg_img.resize((qr_w, qr_h), Image.Resampling.LANCZOS)
                    
                    # Create canvas: Background first, then QR on top
                    canvas = Image.new("RGBA", (qr_w, qr_h))
                    canvas.paste(bg_img, (0,0))
                    canvas.paste(qr_img, (0,0), qr_img) # Use QR as top layer
                    final_img = canvas
        except Exception as e:
            print(f"Background Error: {e}")

    # 8. Apply Floating Stickers (Foreground)
    if stickers_json:
        try:
            stickers = json.loads(stickers_json)
            for s in stickers:
                try:
                    s_img = None
                    if s['src'].startswith('data:image'):
                        head, data = s['src'].split(',', 1)
                        s_img = Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")
                    elif s['src'].startswith('http'):
                        resp = requests.get(s['src'])
                        s_img = Image.open(BytesIO(resp.content)).convert("RGBA")
                    
                    if s_img:
                        target_size = int(qr_w * float(s['size']))
                        s_img = s_img.resize((target_size, target_size), Image.Resampling.LANCZOS)

                        opacity = float(s.get('opacity', 1.0))
                        if opacity < 1.0:
                            alpha = s_img.split()[3]
                            alpha = ImageEnhance.Brightness(alpha).enhance(opacity)
                            s_img.putalpha(alpha)

                        x = int(float(s['x']) * qr_w) - (target_size // 2)
                        y = int(float(s['y']) * qr_h) - (target_size // 2)

                        final_img.paste(s_img, (x, y), s_img)
                except: pass
        except: pass

    buf = BytesIO()
    final_img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')

    return render_template('result.html', qr_image=img_b64, link=link)

@app.route('/scan/<unique_id>', methods=['GET', 'POST'])
def scan_qr(unique_id):
    entry = load_entry(unique_id)
    if not entry: return render_template('error.html', message="Link Invalid")
    if time.time() > entry['expiry']: return render_template('error.html', message="Expired")
    if entry['current_scans'] >= entry['max_scans']: return render_template('error.html', message="Limit Reached")

    if entry['password']:
        if request.method == 'POST':
            if request.form.get('pin') == entry['password']:
                 return serve_content(unique_id, entry)
            else:
                return render_template('password.html', unique_id=unique_id, error="Wrong PIN")
        return render_template('password.html', unique_id=unique_id)

    return serve_content(unique_id, entry)

def serve_content(unique_id, entry):
    if entry['type'] != 'file':
        entry['current_scans'] += 1
        save_entry(unique_id, entry)

    context = {'content': entry['data'], 'unique_id': unique_id}
    if entry['type'] == 'text': return render_template('view_text.html', **context)
    elif entry['type'] == 'audio': return render_template('view_audio.html', **context)
    elif entry['type'] == 'file': return download_file(unique_id)

@app.route('/download/<unique_id>')
def download_file(unique_id):
    entry = load_entry(unique_id)
    if entry and entry['type'] == 'file':
        entry['current_scans'] += 1
        save_entry(unique_id, entry)
        return send_file(entry['data'], as_attachment=True, download_name=os.path.basename(entry['data']))
    return "Error"

@app.route('/audio_file/<unique_id>')
def serve_audio(unique_id):
    entry = load_entry(unique_id)
    if entry: return send_file(entry['data'])
    return "Error", 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port, debug=True)