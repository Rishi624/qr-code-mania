import os
import time
import uuid
import json
from flask import Flask, render_template, request, send_file, url_for, jsonify
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
from PIL import Image, ImageEnhance
from io import BytesIO
import base64
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'codemania_robust_v7')

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
    data_type = request.form.get('type')
    password = request.form.get('password')
    max_scans = request.form.get('max_scans')
    max_scans = int(max_scans) if max_scans and max_scans.strip() else 100
    qr_color_hex = request.form.get('color', '#000000')
    stickers_json = request.form.get('stickers_data')
    background_data = request.form.get('background_data') 
    
    unique_id = str(uuid.uuid4())
    stored_data = ""

    # --- SIMPLIFIED CONTENT HANDLING ---
    if data_type == 'text':
        stored_data = request.form.get('text_content')
        
    elif data_type == 'file' or data_type == 'audio': 
        # Treat Audio EXACTLY like a file
        file_key = 'file_upload' if data_type == 'file' else 'audio_blob'
        
        if file_key in request.files:
            file = request.files[file_key]
            # Use .webm for audio (universal web recording format)
            ext = ".webm" if data_type == 'audio' else os.path.splitext(file.filename)[1]
            filepath = os.path.join(UPLOAD_FOLDER, f"{unique_id}{ext}")
            file.save(filepath)
            stored_data = filepath

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

    # --- QR GENERATION ---
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=2)
    link = url_for('scan_qr', unique_id=unique_id, _external=True)
    qr.add_data(link)
    qr.make(fit=True)

    # Color Logic
    h = qr_color_hex.lstrip('#')
    front_color = tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (255,)
    back_color = (255, 255, 255, 0) if background_data else (255, 255, 255, 255)

    img = qr.make_image(image_factory=StyledPilImage, module_drawer=RoundedModuleDrawer(), color_mask=SolidFillColorMask(front_color=front_color, back_color=back_color))
    qr_img = img.get_image().convert("RGBA")
    final_img = qr_img
    qr_w, qr_h = qr_img.size

    # Background Application
    if background_data:
        try:
            bg_data = json.loads(background_data)
            if 'src' in bg_data:
                bg_src = bg_data['src']
                bg_img = None
                if bg_src.startswith('data:image'):
                    head, data = bg_src.split(',', 1)
                    bg_img = Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")
                elif bg_src.startswith('http'):
                    resp = requests.get(bg_src)
                    bg_img = Image.open(BytesIO(resp.content)).convert("RGBA")
                
                if bg_img:
                    bg_img = bg_img.resize((qr_w, qr_h), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGBA", (qr_w, qr_h))
                    canvas.paste(bg_img, (0,0))
                    canvas.paste(qr_img, (0,0), qr_img)
                    final_img = canvas
        except: pass

    # Sticker Application
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
    # Only files/audio trigger the file download logic
    if entry['type'] == 'text':
        entry['current_scans'] += 1
        save_entry(unique_id, entry)
        return render_template('view_text.html', content=entry['data'], unique_id=unique_id)
    
    # Files and Audio now use the same logic: DOWNLOAD
    elif entry['type'] in ['file', 'audio']:
        return render_template('view_text.html', 
                               content="Secure Content Ready.", 
                               is_file=True, 
                               file_url=url_for('download_file', unique_id=unique_id),
                               unique_id=unique_id)

@app.route('/download/<unique_id>')
def download_file(unique_id):
    entry = load_entry(unique_id)
    if entry:
        entry['current_scans'] += 1
        save_entry(unique_id, entry)
        
        # Smart Naming
        filename = "secure_file"
        if entry['type'] == 'audio':
            filename = "voice_note.webm"
        elif entry['type'] == 'file':
            filename = os.path.basename(entry['data'])
            
        return send_file(entry['data'], as_attachment=True, download_name=filename)
    return "Error"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port, debug=True)