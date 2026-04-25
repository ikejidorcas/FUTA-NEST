from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import requests
import cloudinary
import cloudinary.uploader
import random
import string
from datetime import datetime, timedelta

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-later')

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')

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

TERMII_API_KEY = os.getenv('TERMII_API_KEY')

def send_otp(phone, code):
    """Send OTP via Termii SMS"""
    url = "https://v3.api.termii.com/api/sms/send"
    payload = {
        "to": phone,
        "from": "FUTANEST",
        "sms": f"Your FUTA Nest verification code is: {code}. Valid for 10 minutes. Do not share this code.",
        "type": "plain",
        "api_key": TERMII_API_KEY,
        "channel": "generic"
    }
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except:
        return False

def generate_otp():
    """Generate 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/listings')
def listings():
    area = request.args.get('area', '')
    max_price = request.args.get('max_price', '')

    params = {"approved": "eq.true", "available": "eq.true", "order": "created_at.desc"}
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

@app.route('/post-listing', methods=['GET', 'POST'])
def post_listing():
    # Check if agent is logged in
    if not session.get('agent_phone'):
        flash('Please verify your phone number first to post a listing.', 'danger')
        return redirect('/agent/register')
    
    # Check if agent is blocked
    phone = session.get('agent_phone')
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

@app.route('/admin/approve/<listing_id>')
def approve_listing(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "listings",
                     data={"approved": True},
                     params={"id": f"eq.{listing_id}"})
    flash('Listing approved!', 'success')
    return redirect('/admin/dashboard')

@app.route('/admin/delete/<listing_id>')
def delete_listing(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("DELETE", "listings",
                     params={"id": f"eq.{listing_id}"})
    flash('Listing deleted!', 'success')
    return redirect('/admin/dashboard')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/')


@app.route('/taken/<listing_id>')
def mark_taken(listing_id):
    response = supabase_request("GET", "listings",
                                params={"id": f"eq.{listing_id}"})
    listing = response.json()[0] if response.json() else {}
    
    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"})
    
    # Send WhatsApp notification to you
    your_number = "2349050638087"
    message = f"FUTA Nest Alert: '{listing.get('title', 'A listing')}' in {listing.get('area', '')} has been marked as TAKEN by agent {listing.get('agent_name', '')}."
    whatsapp_url = f"https://wa.me/{your_number}?text={message}"
    
    return render_template('taken.html', whatsapp_url=whatsapp_url)

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
    
    # Get listing details
    response = supabase_request("GET", "listings",
                                params={"id": f"eq.{listing_id}"})
    listing = response.json()[0] if response.json() else {}
    return render_template('report.html', listing=listing)

@app.route('/admin/reports')
def admin_reports():
    if not session.get('admin'):
        return redirect('/admin')
    response = supabase_request("GET", "reports",
                                params={"order": "created_at.desc"})
    reports = response.json() if response.status_code == 200 else []
    return render_template('admin_reports.html', reports=reports)

# ── AGENT REGISTER ───────────────────────────────────────────────
@app.route('/agent/register', methods=['GET', 'POST'])
def agent_register():
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone').strip().replace(' ', '')
        
        # Make sure phone starts with country code
        if phone.startswith('0'):
            phone = '234' + phone[1:]
        
        # Check if agent is blocked
        blocked_check = supabase_request("GET", "agents",
                                         params={"phone": f"eq.{phone}",
                                                 "blocked": "eq.true"})
        if blocked_check.json():
            flash('This number has been blocked from FUTA Nest.', 'danger')
            return redirect('/agent/register')
        
        # Generate and send OTP
        code = generate_otp()
        expires = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        
        # Save OTP to database
        supabase_request("POST", "otps", data={
            "phone": phone,
            "code": code,
            "expires_at": expires,
            "used": False
        })
        
        # Send OTP via Termii
        sent = send_otp(phone, code)
        
        if sent:
            session['pending_phone'] = phone
            session['pending_name'] = name
            flash(f'OTP sent to {phone}. Enter it below to verify.', 'success')
            return redirect('/agent/verify-otp')
        else:
            flash('Could not send OTP. Please check your number and try again.', 'danger')
    
    return render_template('agent_register.html')

# ── VERIFY OTP ───────────────────────────────────────────────────
@app.route('/agent/verify-otp', methods=['GET', 'POST'])
def agent_verify_otp():
    if request.method == 'POST':
        code = request.form.get('otp').strip()
        phone = session.get('pending_phone')
        name = session.get('pending_name')
        
        if not phone:
            return redirect('/agent/register')
        
        # Check OTP
        now = datetime.utcnow().isoformat()
        otp_check = supabase_request("GET", "otps",
                                      params={"phone": f"eq.{phone}",
                                              "code": f"eq.{code}",
                                              "used": "eq.false",
                                              "order": "created_at.desc",
                                              "limit": "1"})
        
        otps = otp_check.json()
        
        if not otps:
            flash('Invalid OTP. Please try again.', 'danger')
            return redirect('/agent/verify-otp')
        
        otp = otps[0]
        
        # Check if expired
        expires_at = datetime.fromisoformat(otp['expires_at'].replace('Z', ''))
        if datetime.utcnow() > expires_at:
            flash('OTP has expired. Please request a new one.', 'danger')
            return redirect('/agent/register')
        
        # Mark OTP as used
        supabase_request("PATCH", "otps",
                         data={"used": True},
                         params={"id": f"eq.{otp['id']}"})
        
        # Create or update agent account
        existing = supabase_request("GET", "agents",
                                     params={"phone": f"eq.{phone}"})
        
        if not existing.json():
            supabase_request("POST", "agents", data={
                "phone": phone,
                "name": name,
                "verified": True,
                "flagged": False,
                "blocked": False
            })
        else:
            supabase_request("PATCH", "agents",
                             data={"verified": True},
                             params={"phone": f"eq.{phone}"})
        
        # Log agent in
        session['agent_phone'] = phone
        session['agent_name'] = name
        session.pop('pending_phone', None)
        session.pop('pending_name', None)
        
        flash('Phone verified! You can now post listings.', 'success')
        return redirect('/post-listing')
    
    return render_template('agent_verify_otp.html')

# ── AGENT LOGOUT ─────────────────────────────────────────────────
@app.route('/agent/logout')
def agent_logout():
    session.pop('agent_phone', None)
    session.pop('agent_name', None)
    return redirect('/')

# ── ADMIN FLAG AGENT ─────────────────────────────────────────────
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

# ── ADMIN BLOCK AGENT ─────────────────────────────────────────────
@app.route('/admin/block/<phone>')
def block_agent(phone):
    if not session.get('admin'):
        return redirect('/admin')
    # Block agent
    supabase_request("PATCH", "agents",
                     data={"blocked": True, "flagged": True},
                     params={"phone": f"eq.{phone}"})
    # Remove all their listings
    supabase_request("PATCH", "listings",
                     data={"approved": False, "available": False},
                     params={"phone": f"eq.{phone}"})
    flash(f'Agent {phone} blocked and listings removed!', 'success')
    return redirect('/admin/agents')

# ── ADMIN UNBLOCK AGENT ───────────────────────────────────────────
@app.route('/admin/unblock/<phone>')
def unblock_agent(phone):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "agents",
                     data={"blocked": False, "flagged": False, "flag_reason": None},
                     params={"phone": f"eq.{phone}"})
    flash(f'Agent {phone} has been unblocked!', 'success')
    return redirect('/admin/agents')

# ── ADMIN VIEW AGENTS ─────────────────────────────────────────────
@app.route('/admin/agents')
def admin_agents():
    if not session.get('admin'):
        return redirect('/admin')
    response = supabase_request("GET", "agents",
                                params={"order": "created_at.desc"})
    agents = response.json() if response.status_code == 200 else []
    return render_template('admin_agents.html', agents=agents)

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)
    