from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import requests
import cloudinary
import cloudinary.uploader
import random
import string
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-later')

# Supabase config
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
TERMII_API_KEY = os.getenv('TERMII_API_KEY')

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', 'da6gxwgjq'),
    api_key=os.getenv('CLOUDINARY_API_KEY', '593233257222916'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', 'XZCJn5dh6jnFAr1-pgz2J1ntLzQ')
)

def supabase_request(method, endpoint, data=None, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    if method == "GET":
        response = requests.get(url, headers=headers, params=params)
    elif method == "POST":
        response = requests.post(url, headers=headers, json=data)
    elif method == "PATCH":
        response = requests.patch(url, headers=headers, json=data, params=params)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers, params=params)
    return response

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def send_otp(phone, code):
    try:
        if phone.startswith('0'):
            phone = '234' + phone[1:]
        url = "https://v3.api.termii.com/api/sms/send"
        payload = {
            "api_key": TERMII_API_KEY,
            "to": phone,
            "from": "FUTANEST",
            "sms": f"Your FUTA Nest verification code is: {code}. Valid for 10 minutes. Do not share.",
            "type": "plain",
            "channel": "generic"
        }
        response = requests.post(url, json=payload)
        print("Termii response:", response.status_code, response.text)
        return response.status_code == 200
    except Exception as e:
        print("Termii error:", e)
        return False

# ── HOME PAGE ────────────────────────────────────────────────────
@app.route('/')
def home():
    return render_template('home.html')

# ── LISTINGS PAGE ────────────────────────────────────────────────
@app.route('/listings')
def listings():
    area = request.args.get('area', '')
    max_price = request.args.get('max_price', '')

    params = {"approved": "eq.true", "available": "eq.true", "order": "featured.desc,created_at.desc"}
    if area:
        params["area"] = f"eq.{area}"
    if max_price:
        params["price"] = f"lte.{max_price}"

    response = supabase_request("GET", "listings", params=params)
    all_listings = response.json() if response.status_code == 200 else []

    return render_template('listings.html',
                           listings=all_listings,
                           area=area,
                           max_price=max_price)



# ── AGENT LOGOUT ─────────────────────────────────────────────────
@app.route('/agent/logout')
def agent_logout():
    session.pop('agent_phone', None)
    session.pop('agent_name', None)
    return redirect('/')

# ── POST LISTING PAGE ────────────────────────────────────────────
@app.route('/post-listing', methods=['GET', 'POST'])
def post_listing():
    
    if not session.get('agent_phone') and not session.get('admin'):
        flash('Please verify your phone number first to post a listing.', 'danger')
        return redirect('/agent/register')

    phone = session.get('agent_phone') or 'admin'

    

    blocked_check = supabase_request("GET", "agents",
                                     params={"phone": f"eq.{phone}",
                                             "blocked": "eq.true"})
    if blocked_check.json():
        flash('Your account has been blocked. Contact admin for support.', 'danger')
        return redirect('/')

    if request.method == 'POST':
        image_url = ''
        video_url = ''

        image_file = request.files.get('image')
        if image_file and image_file.filename != '':
            image_upload = cloudinary.uploader.upload(
                image_file, folder="futa-nest/images")
            image_url = image_upload.get('secure_url', '')

        video_file = request.files.get('video')
        if video_file and video_file.filename != '':
            video_upload = cloudinary.uploader.upload(
                video_file, resource_type="video", folder="futa-nest/videos")
            video_url = video_upload.get('secure_url', '')

        data = {
            "agent_name": session.get('agent_name'),
            "phone": phone,
            "title": request.form.get('title'),
            "description": request.form.get('description'),
            "area": request.form.get('area'),
            "rooms": int(request.form.get('rooms')),
            "price": int(request.form.get('price')),
            "image_url": image_url,
            "video_url": video_url,
            "approved": False,
            "available": True,
            "featured": False,
            "verified": False,
            "agent_phone_ref": phone
        }

        response = supabase_request("POST", "listings", data=data)
        if response.status_code == 201:
            flash('Listing submitted! It will appear after admin approval.', 'success')
        else:
            flash(f'Error: {response.text}', 'danger')
        return redirect('/post-listing')

    return render_template('post_listing.html',
                           agent_name=session.get('agent_name'),
                           agent_phone=phone)

# ── REPORT LISTING ───────────────────────────────────────────────
@app.route('/report/<listing_id>', methods=['GET', 'POST'])
def report_listing(listing_id):
    if request.method == 'POST':
        data = {
            "listing_id": listing_id,
            "reason": request.form.get('reason'),
            "reporter_phone": request.form.get('reporter_phone', '')
        }
        supabase_request("POST", "reports", data=data)
        flash('Report submitted! We will investigate immediately.', 'success')
        return redirect('/listings')

    response = supabase_request("GET", "listings",
                                params={"id": f"eq.{listing_id}"})
    listing = response.json()[0] if response.json() else {}
    return render_template('report.html', listing=listing)

# ── MARK AS TAKEN (AGENT LINK) ───────────────────────────────────
@app.route('/taken/<listing_id>')
def mark_taken(listing_id):
    response = supabase_request("GET", "listings",
                                params={"id": f"eq.{listing_id}"})
    listing = response.json()[0] if response.json() else {}

    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"})

    your_number = "2349050638087"
    message = f"FUTA Nest Alert: '{listing.get('title', 'A listing')}' in {listing.get('area', '')} has been marked as TAKEN by agent {listing.get('agent_name', '')}."
    whatsapp_url = f"https://wa.me/{your_number}?text={message}"

    return render_template('taken.html', whatsapp_url=whatsapp_url)

# ── FEATURE PAGE ─────────────────────────────────────────────────
@app.route('/feature')
def feature():
    return render_template('feature.html')

# ── VERIFY AGENT PAGE ─────────────────────────────────────────────
@app.route('/verify', methods=['GET', 'POST'])
def verify_agent():
    if request.method == 'POST':
        agent_id_file = request.files.get('agent_id')
        agent_id_url = ''
        if agent_id_file and agent_id_file.filename != '':
            id_upload = cloudinary.uploader.upload(
                agent_id_file, folder="futa-nest/ids")
            agent_id_url = id_upload.get('secure_url', '')

        data = {
            "agent_name": request.form.get('agent_name'),
            "phone": request.form.get('phone'),
            "id_type": request.form.get('id_type'),
            "id_url": agent_id_url,
            "verified": False
        }
        supabase_request("POST", "verifications", data=data)
        flash('Verification submitted! We will review and contact you within 24 hours.', 'success')
        return redirect('/verify')
    return render_template('verify.html')

# ── ADMIN LOGIN ──────────────────────────────────────────────────
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect('/admin/dashboard')
        else:
            flash('Wrong password!', 'danger')
    return render_template('admin_login.html')

# ── ADMIN DASHBOARD ──────────────────────────────────────────────
@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin'):
        return redirect('/admin')

    all_response = supabase_request("GET", "listings",
                                    params={"order": "created_at.desc"})
    pending_response = supabase_request("GET", "listings",
                                        params={"approved": "eq.false",
                                                "order": "created_at.desc"})

    all_listings = all_response.json() if all_response.status_code == 200 else []
    pending = pending_response.json() if pending_response.status_code == 200 else []

    return render_template('admin_dashboard.html',
                           all_listings=all_listings,
                           pending=pending)

# ── APPROVE LISTING ──────────────────────────────────────────────
@app.route('/admin/approve/<listing_id>')
def approve_listing(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "listings",
                     data={"approved": True},
                     params={"id": f"eq.{listing_id}"})
    flash('Listing approved!', 'success')
    return redirect('/admin/dashboard')

# ── MARK TAKEN FROM ADMIN ─────────────────────────────────────────
@app.route('/admin/taken/<listing_id>')
def admin_mark_taken(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"})
    flash('Listing marked as taken!', 'success')
    return redirect('/admin/dashboard')

# ── FEATURE LISTING FROM ADMIN ────────────────────────────────────
@app.route('/admin/feature/<listing_id>')
def feature_listing(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "listings",
                     data={"featured": True},
                     params={"id": f"eq.{listing_id}"})
    flash('Listing marked as featured!', 'success')
    return redirect('/admin/dashboard')

# ── DELETE LISTING ───────────────────────────────────────────────
@app.route('/admin/delete/<listing_id>')
def delete_listing(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("DELETE", "listings",
                     params={"id": f"eq.{listing_id}"})
    flash('Listing deleted!', 'success')
    return redirect('/admin/dashboard')

# ── ADMIN REPORTS ─────────────────────────────────────────────────
@app.route('/admin/reports')
def admin_reports():
    if not session.get('admin'):
        return redirect('/admin')
    response = supabase_request("GET", "reports",
                                params={"order": "created_at.desc"})
    reports = response.json() if response.status_code == 200 else []
    return render_template('admin_reports.html', reports=reports)

# ── ADMIN AGENTS ──────────────────────────────────────────────────
@app.route('/admin/agents')
def admin_agents():
    if not session.get('admin'):
        return redirect('/admin')
    response = supabase_request("GET", "agents",
                                params={"order": "created_at.desc"})
    agents = response.json() if response.status_code == 200 else []
    return render_template('admin_agents.html', agents=agents)

# ── FLAG AGENT ────────────────────────────────────────────────────
@app.route('/admin/flag/<phone>')
def flag_agent(phone):
    if not session.get('admin'):
        return redirect('/admin')
    reason = request.args.get('reason', 'Flagged by admin')
    supabase_request("PATCH", "agents",
                     data={"flagged": True, "flag_reason": reason},
                     params={"phone": f"eq.{phone}"})
    flash(f'Agent {phone} has been flagged!', 'success')
    return redirect('/admin/agents')

# ── BLOCK AGENT ───────────────────────────────────────────────────
@app.route('/admin/block/<phone>')
def block_agent(phone):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "agents",
                     data={"blocked": True, "flagged": True},
                     params={"phone": f"eq.{phone}"})
    supabase_request("PATCH", "listings",
                     data={"approved": False, "available": False},
                     params={"phone": f"eq.{phone}"})
    flash(f'Agent {phone} blocked and listings removed!', 'success')
    return redirect('/admin/agents')

# ── UNBLOCK AGENT ─────────────────────────────────────────────────
@app.route('/admin/unblock/<phone>')
def unblock_agent(phone):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "agents",
                     data={"blocked": False, "flagged": False, "flag_reason": None},
                     params={"phone": f"eq.{phone}"})
    flash(f'Agent {phone} has been unblocked!', 'success')
    return redirect('/admin/agents')

# ── ADMIN VERIFICATIONS ───────────────────────────────────────────
@app.route('/admin/verifications')
def admin_verifications():
    if not session.get('admin'):
        return redirect('/admin')
    response = supabase_request("GET", "verifications",
                                params={"order": "created_at.desc"})
    verifications = response.json() if response.status_code == 200 else []
    return render_template('admin_verifications.html', verifications=verifications)

# ── VERIFY AGENT APPROVE ──────────────────────────────────────────
@app.route('/admin/verify-agent/<verification_id>/<phone>')
def verify_agent_approve(verification_id, phone):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "verifications",
                     data={"verified": True},
                     params={"id": f"eq.{verification_id}"})
    supabase_request("PATCH", "listings",
                     data={"verified": True},
                     params={"phone": f"eq.{phone}"})
    flash('Agent verified! All their listings now show verified badge.', 'success')
    return redirect('/admin/verifications')

# ── ADMIN LOGOUT ─────────────────────────────────────────────────
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)