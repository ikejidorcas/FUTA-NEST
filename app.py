from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import requests
import cloudinary
import cloudinary.uploader
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import random
import string
from datetime import datetime, timezone

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-later')

# Rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["2000 per day", "200 per hour"],
    storage_uri="memory://"
)

# Supabase config
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')

# SMS config (Termii)
TERMII_API_KEY = os.getenv('TERMII_API_KEY', '')
TERMII_SENDER_ID = os.getenv('TERMII_SENDER_ID', 'Rentiva')

# Cloudinary config
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', 'da6gxwgjq'),
    api_key=os.getenv('CLOUDINARY_API_KEY', '593233257222916'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', 'XZCJn5dh6jnFAr1-pgz2J1ntLzQ')
)

def supabase_request(method, endpoint, data=None, params=None, admin=False):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    key = SUPABASE_SERVICE_KEY if admin else SUPABASE_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
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

# ── OTP HELPERS ───────────────────────────────────────────────────
def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

def send_otp_sms(phone, otp):
    """Send OTP via Termii SMS. Falls back gracefully if not configured."""
    if not TERMII_API_KEY:
        print(f"[DEV] OTP for {phone}: {otp}")
        return True
    try:
        payload = {
            "to": phone,
            "from": TERMII_SENDER_ID,
            "sms": f"Your Rentiva verification code is: {otp}. Valid for 10 minutes. Do not share this code.",
            "type": "plain",
            "api_key": TERMII_API_KEY,
            "channel": "dnd"
        }
        res = requests.post("https://api.ng.termii.com/api/sms/send", json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"SMS error: {e}")
        return False

def store_otp(phone, otp):
    """Store OTP in Supabase, invalidate old ones first."""
    # Invalidate previous OTPs for this phone
    supabase_request("PATCH", "otp_codes",
                     data={"used": True},
                     params={"phone": f"eq.{phone}", "used": "eq.false"},
                     admin=True)
    # Store new OTP
    supabase_request("POST", "otp_codes",
                     data={"phone": phone, "code": otp, "used": False},
                     admin=True)

def verify_otp(phone, code):
    """Verify OTP — returns True if valid and not expired."""
    res = supabase_request("GET", "otp_codes",
                           params={
                               "phone": f"eq.{phone}",
                               "code": f"eq.{code}",
                               "used": "eq.false",
                               "order": "created_at.desc",
                               "limit": "1"
                           },
                           admin=True)
    if res.status_code != 200 or not res.json():
        return False

    record = res.json()[0]
    # Check expiry
    expires_str = record.get('expires_at', '')
    if expires_str:
        try:
            expires = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
            if datetime.now(timezone.utc) > expires:
                return False
        except Exception:
            pass

    # Mark as used
    supabase_request("PATCH", "otp_codes",
                     data={"used": True},
                     params={"id": f"eq.{record['id']}"},
                     admin=True)
    return True

# ── SECURITY HEADERS ─────────────────────────────────────────────
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# ── HOME PAGE ────────────────────────────────────────────────────
@app.route('/')
def home():
    amb_response = supabase_request("GET", "ambassadors",
                                    params={"is_active": "eq.true",
                                            "order": "created_at.asc"},
                                    admin=True)
    ambassadors = amb_response.json() if amb_response.status_code == 200 else []
    return render_template('home.html', ambassadors=ambassadors)

# ── LISTINGS PAGE ────────────────────────────────────────────────
@app.route('/listings')
def listings():
    area = request.args.get('area', '')
    max_price = request.args.get('max_price', '')

    params = {"approved": "eq.true", "available": "eq.true",
              "order": "featured.desc,created_at.desc"}
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

# ── POST LISTING ─────────────────────────────────────────────────
@app.route('/post-listing', methods=['GET', 'POST'])
@limiter.limit("10 per hour", methods=["POST"])
def post_listing():
    if request.method == 'POST':
        image_url = ''
        video_url = ''

        agent_name = request.form.get('agent_name')
        phone = request.form.get('phone', '').strip().replace(' ', '')

        if not agent_name or not phone:
            flash('Name and phone number are required.', 'danger')
            return redirect('/post-listing')

        if phone.startswith('0'):
            phone = '234' + phone[1:]

        blocked_check = supabase_request("GET", "agents",
                                         params={"phone": f"eq.{phone}",
                                                 "blocked": "eq.true"})
        if blocked_check.json():
            flash('Your number has been blocked from Rentiva.', 'danger')
            return redirect('/')

        price = int(request.form.get('price', 0))

        if price < 30000:
            flash('Price seems too low. Minimum price is ₦30,000/year.', 'danger')
            return redirect('/post-listing')
        if price > 2000000:
            flash('Price seems unusually high.', 'danger')
            return redirect('/post-listing')

        duplicate_check = supabase_request("GET", "listings",
                                           params={"phone": f"eq.{phone}",
                                                   "area": f"eq.{request.form.get('area')}",
                                                   "price": f"eq.{price}",
                                                   "available": "eq.true"})
        if duplicate_check.json():
            flash('You already have a listing with the same area and price.', 'danger')
            return redirect('/post-listing')

        flagged = price < 50000 or price > 1000000

        image_file = request.files.get('image')
        if image_file and image_file.filename != '':
            image_upload = cloudinary.uploader.upload(
                image_file, folder="rentiva/images")
            image_url = image_upload.get('secure_url', '')

        video_file = request.files.get('video')
        if video_file and video_file.filename != '':
            video_upload = cloudinary.uploader.upload(
                video_file, resource_type="video", folder="rentiva/videos")
            video_url = video_upload.get('secure_url', '')

        existing_agent = supabase_request("GET", "agents",
                                          params={"phone": f"eq.{phone}"})
        if not existing_agent.json():
            supabase_request("POST", "agents", data={
                "phone": phone,
                "name": agent_name,
                "verified": False,
                "flagged": False,
                "blocked": False,
                "verification_status": "none"
            })

        data = {
            "agent_name": agent_name,
            "phone": phone,
            "title": request.form.get('title'),
            "description": request.form.get('description'),
            "area": request.form.get('area'),
            "rooms": int(request.form.get('rooms')),
            "price": price,
            "image_url": image_url,
            "video_url": video_url,
            "approved": False,
            "available": True,
            "featured": False,
            "verified": False,
            "flagged": flagged,
            "agent_phone_ref": phone
        }

        response = supabase_request("POST", "listings", data=data)
        if response.status_code == 201:
            flash('Listing submitted! It will appear after admin approval.', 'success')
        else:
            flash(f'Error: {response.text}', 'danger')
        return redirect('/post-listing')

    return render_template('post_listing.html')

# ── AGENT REGISTER ───────────────────────────────────────────────
@app.route('/agent/register', methods=['GET', 'POST'])
def agent_register():
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone', '').strip().replace(' ', '')

        if phone.startswith('0'):
            phone = '234' + phone[1:]

        blocked_check = supabase_request("GET", "agents",
                                         params={"phone": f"eq.{phone}",
                                                 "blocked": "eq.true"})
        if blocked_check.json():
            flash('This number has been blocked from Rentiva.', 'danger')
            return redirect('/agent/register')

        existing = supabase_request("GET", "agents",
                                    params={"phone": f"eq.{phone}"})
        if not existing.json():
            supabase_request("POST", "agents", data={
                "phone": phone,
                "name": name,
                "verified": False,
                "flagged": False,
                "blocked": False,
                "verification_status": "none"
            })

        session['agent_phone'] = phone
        session['agent_name'] = name
        flash(f'Welcome {name}! You can now post your listing.', 'success')
        return redirect('/post-listing')

    return render_template('agent_register.html')

# ── AGENT LOGOUT ─────────────────────────────────────────────────
@app.route('/agent/logout')
def agent_logout():
    session.pop('agent_phone', None)
    session.pop('agent_name', None)
    return redirect('/')

# ── REPORT LISTING ───────────────────────────────────────────────
@app.route('/report/<listing_id>', methods=['GET', 'POST'])
@limiter.limit("5 per hour")
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

# ── MARK AS TAKEN ────────────────────────────────────────────────
@app.route('/taken/<listing_id>')
def mark_taken(listing_id):
    response = supabase_request("GET", "listings",
                                params={"id": f"eq.{listing_id}"})
    listing = response.json()[0] if response.json() else {}

    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"})

    your_number = "2349050638087"
    message = f"Rentiva Alert: '{listing.get('title', 'A listing')}' in {listing.get('area', '')} has been marked as TAKEN by agent {listing.get('agent_name', '')}."
    whatsapp_url = f"https://wa.me/{your_number}?text={message}"
    return render_template('taken.html', whatsapp_url=whatsapp_url)

# ── FEATURE PAGE ─────────────────────────────────────────────────
@app.route('/feature')
def feature():
    return render_template('feature.html')

# ══════════════════════════════════════════════════════════════════
# VERIFIED AGENT SYSTEM
# ══════════════════════════════════════════════════════════════════

# ── STEP 1: VERIFY PAGE (entry + status check) ───────────────────
@app.route('/verify', methods=['GET', 'POST'])
@limiter.limit("20 per hour")
def verify_agent():
    # Check if agent is looking up their status
    if request.method == 'GET':
        phone_lookup = request.args.get('phone', '').strip().replace(' ', '')
        if phone_lookup:
            if phone_lookup.startswith('0'):
                phone_lookup = '234' + phone_lookup[1:]
            existing = supabase_request("GET", "verifications",
                                        params={"phone": f"eq.{phone_lookup}",
                                                "order": "created_at.desc",
                                                "limit": "1"},
                                        admin=True)
            ver = existing.json()[0] if existing.json() else None
            return render_template('verify.html', status_check=ver,
                                   phone_checked=phone_lookup)
        return render_template('verify.html')

    # POST — initiate verification, send OTP
    phone = request.form.get('phone', '').strip().replace(' ', '')
    if phone.startswith('0'):
        phone = '234' + phone[1:]

    agent_name = request.form.get('agent_name', '').strip()
    email = request.form.get('email', '').strip()

    if not phone or not agent_name:
        flash('Name and phone number are required.', 'danger')
        return redirect('/verify')

    # Check if already approved
    existing_ver = supabase_request("GET", "verifications",
                                    params={"phone": f"eq.{phone}",
                                            "status": "eq.approved"},
                                    admin=True)
    if existing_ver.json():
        flash('This number is already verified on Rentiva!', 'success')
        return redirect('/verify')

    # Generate and send OTP
    otp = generate_otp()
    store_otp(phone, otp)
    sms_sent = send_otp_sms(phone, otp)

    # Store details in session for next step
    session['verify_phone'] = phone
    session['verify_name'] = agent_name
    session['verify_email'] = email

    if sms_sent:
        flash(f'A 6-digit code has been sent to {phone}. Enter it below.', 'success')
    else:
        flash('OTP sent! Check your phone for the 6-digit code.', 'success')

    return render_template('verify.html', step='otp', phone=phone)

# ── STEP 2: VERIFY OTP ───────────────────────────────────────────
@app.route('/verify/confirm-otp', methods=['POST'])
@limiter.limit("10 per hour")
def confirm_otp():
    phone = session.get('verify_phone', '')
    otp_entered = request.form.get('otp', '').strip()

    if not phone:
        flash('Session expired. Please start again.', 'danger')
        return redirect('/verify')

    if not verify_otp(phone, otp_entered):
        flash('Invalid or expired code. Please try again.', 'danger')
        return render_template('verify.html', step='otp', phone=phone)

    session['otp_verified'] = True
    flash('Phone number verified! Now upload your ID and selfie.', 'success')
    return render_template('verify.html', step='upload',
                           phone=phone,
                           agent_name=session.get('verify_name', ''))

# ── STEP 3: UPLOAD ID + SELFIE ───────────────────────────────────
@app.route('/verify/upload', methods=['POST'])
@limiter.limit("5 per hour")
def verify_upload():
    if not session.get('otp_verified'):
        flash('Please verify your phone number first.', 'danger')
        return redirect('/verify')

    phone = session.get('verify_phone', '')
    agent_name = session.get('verify_name', '')
    email = session.get('verify_email', '')

    if not phone:
        flash('Session expired. Please start again.', 'danger')
        return redirect('/verify')

    agent_id_url = ''
    selfie_url = ''

    # Upload government ID
    agent_id_file = request.files.get('agent_id')
    if agent_id_file and agent_id_file.filename != '':
        id_upload = cloudinary.uploader.upload(
            agent_id_file,
            folder="rentiva/ids",
            resource_type="image",
            access_mode="authenticated"  # private — not publicly guessable
        )
        agent_id_url = id_upload.get('secure_url', '')
    else:
        flash('Government ID is required.', 'danger')
        return render_template('verify.html', step='upload',
                               phone=phone, agent_name=agent_name)

    # Upload selfie
    selfie_file = request.files.get('selfie')
    if selfie_file and selfie_file.filename != '':
        selfie_upload = cloudinary.uploader.upload(
            selfie_file,
            folder="rentiva/selfies",
            resource_type="image",
            access_mode="authenticated"
        )
        selfie_url = selfie_upload.get('secure_url', '')
    else:
        flash('Selfie photo is required.', 'danger')
        return render_template('verify.html', step='upload',
                               phone=phone, agent_name=agent_name)

    # Save verification record
    ver_data = {
        "agent_name": agent_name,
        "phone": phone,
        "id_type": request.form.get('id_type', 'government_id'),
        "id_url": agent_id_url,
        "selfie_url": selfie_url,
        "verified": False,
        "status": "pending",
        "otp_verified": True
    }

    # Add email to agents table if provided
    if email:
        ver_data["email"] = email
        supabase_request("PATCH", "agents",
                         data={"email": email},
                         params={"phone": f"eq.{phone}"},
                         admin=True)

    # Check if pending already exists — update instead of duplicate
    existing = supabase_request("GET", "verifications",
                                params={"phone": f"eq.{phone}",
                                        "status": "eq.pending"},
                                admin=True)
    if existing.json():
        supabase_request("PATCH", "verifications",
                         data=ver_data,
                         params={"phone": f"eq.{phone}",
                                 "status": "eq.pending"},
                         admin=True)
    else:
        supabase_request("POST", "verifications", data=ver_data, admin=True)

    # Update agent verification_status to pending
    supabase_request("PATCH", "agents",
                     data={"verification_status": "pending"},
                     params={"phone": f"eq.{phone}"},
                     admin=True)

    # Clear session verify flags
    session.pop('otp_verified', None)
    session.pop('verify_phone', None)
    session.pop('verify_name', None)
    session.pop('verify_email', None)

    flash('Verification submitted! We will review within 24 hours.', 'success')
    return render_template('verify.html', step='done', phone=phone)

# ── RESEND OTP ───────────────────────────────────────────────────
@app.route('/verify/resend-otp', methods=['POST'])
@limiter.limit("3 per hour")
def resend_otp():
    phone = session.get('verify_phone', '')
    if not phone:
        flash('Session expired. Please start again.', 'danger')
        return redirect('/verify')

    otp = generate_otp()
    store_otp(phone, otp)
    send_otp_sms(phone, otp)
    flash('A new code has been sent to your phone.', 'success')
    return render_template('verify.html', step='otp', phone=phone)

# ══════════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════

# ── ADMIN LOGIN ──────────────────────────────────────────────────
@app.route('/futanest-control-2025', methods=['GET', 'POST'])
@limiter.limit("20 per hour")
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
        return redirect('/futanest-control-2025')

    all_response = supabase_request("GET", "listings",
                                    params={"order": "created_at.desc"}, admin=True)
    pending_response = supabase_request("GET", "listings",
                                        params={"approved": "eq.false",
                                                "order": "created_at.desc"}, admin=True)
    pending_ver_response = supabase_request("GET", "verifications",
                                            params={"status": "eq.pending"}, admin=True)

    all_listings = all_response.json() if all_response.status_code == 200 else []
    pending = pending_response.json() if pending_response.status_code == 200 else []
    pending_verifications_count = len(pending_ver_response.json()) if pending_ver_response.status_code == 200 else 0

    return render_template('admin_dashboard.html',
                           all_listings=all_listings,
                           pending=pending,
                           pending_verifications_count=pending_verifications_count)

# ── APPROVE LISTING ──────────────────────────────────────────────
@app.route('/admin/approve/<listing_id>')
def approve_listing(listing_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "listings",
                     data={"approved": True},
                     params={"id": f"eq.{listing_id}"}, admin=True)
    flash('Listing approved!', 'success')
    return redirect('/admin/dashboard')

# ── MARK TAKEN FROM ADMIN ─────────────────────────────────────────
@app.route('/admin/taken/<listing_id>')
def admin_mark_taken(listing_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"}, admin=True)
    flash('Listing marked as taken!', 'success')
    return redirect('/admin/dashboard')

# ── FEATURE LISTING FROM ADMIN ────────────────────────────────────
@app.route('/admin/feature/<listing_id>')
def feature_listing(listing_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "listings",
                     data={"featured": True},
                     params={"id": f"eq.{listing_id}"}, admin=True)
    flash('Listing marked as featured!', 'success')
    return redirect('/admin/dashboard')

# ── DELETE LISTING ───────────────────────────────────────────────
@app.route('/admin/delete/<listing_id>')
def delete_listing(listing_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("DELETE", "listings",
                     params={"id": f"eq.{listing_id}"}, admin=True)
    flash('Listing deleted!', 'success')
    return redirect('/admin/dashboard')

# ── ADMIN REPORTS ─────────────────────────────────────────────────
@app.route('/admin/reports')
def admin_reports():
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    response = supabase_request("GET", "reports",
                                params={"order": "created_at.desc"}, admin=True)
    reports = response.json() if response.status_code == 200 else []
    return render_template('admin_reports.html', reports=reports)

# ── ADMIN AGENTS ──────────────────────────────────────────────────
@app.route('/admin/agents')
def admin_agents():
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    response = supabase_request("GET", "agents",
                                params={"order": "created_at.desc"}, admin=True)
    agents = response.json() if response.status_code == 200 else []
    return render_template('admin_agents.html', agents=agents)

# ── FLAG AGENT ────────────────────────────────────────────────────
@app.route('/admin/flag/<phone>')
def flag_agent(phone):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    reason = request.args.get('reason', 'Flagged by admin')
    supabase_request("PATCH", "agents",
                     data={"flagged": True, "flag_reason": reason},
                     params={"phone": f"eq.{phone}"}, admin=True)
    flash(f'Agent {phone} has been flagged!', 'success')
    return redirect('/admin/agents')

# ── BLOCK AGENT ───────────────────────────────────────────────────
@app.route('/admin/block/<phone>')
def block_agent(phone):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "agents",
                     data={"blocked": True, "flagged": True},
                     params={"phone": f"eq.{phone}"}, admin=True)
    supabase_request("PATCH", "listings",
                     data={"approved": False, "available": False},
                     params={"phone": f"eq.{phone}"}, admin=True)
    flash(f'Agent {phone} blocked and listings removed!', 'success')
    return redirect('/admin/agents')

# ── UNBLOCK AGENT ─────────────────────────────────────────────────
@app.route('/admin/unblock/<phone>')
def unblock_agent(phone):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "agents",
                     data={"blocked": False, "flagged": False, "flag_reason": None},
                     params={"phone": f"eq.{phone}"}, admin=True)
    flash(f'Agent {phone} has been unblocked!', 'success')
    return redirect('/admin/agents')

# ── ADMIN VERIFICATIONS ───────────────────────────────────────────
@app.route('/admin/verifications')
def admin_verifications():
    if not session.get('admin'):
        return redirect('/futanest-control-2025')

    status_filter = request.args.get('status', 'pending')
    params = {"order": "created_at.desc"}
    if status_filter != 'all':
        params["status"] = f"eq.{status_filter}"

    response = supabase_request("GET", "verifications", params=params, admin=True)
    verifications = response.json() if response.status_code == 200 else []

    # Count by status for tabs
    all_ver = supabase_request("GET", "verifications",
                               params={"order": "created_at.desc"}, admin=True)
    all_v = all_ver.json() if all_ver.status_code == 200 else []
    counts = {
        "all": len(all_v),
        "pending": sum(1 for v in all_v if v.get('status') == 'pending'),
        "approved": sum(1 for v in all_v if v.get('status') == 'approved'),
        "rejected": sum(1 for v in all_v if v.get('status') == 'rejected'),
    }

    return render_template('admin_verifications.html',
                           verifications=verifications,
                           status_filter=status_filter,
                           counts=counts)

# ── APPROVE VERIFICATION ──────────────────────────────────────────
@app.route('/admin/verify-agent/<verification_id>/<phone>')
def verify_agent_approve(verification_id, phone):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')

    now_iso = datetime.now(timezone.utc).isoformat()

    supabase_request("PATCH", "verifications",
                     data={"verified": True, "status": "approved",
                           "reviewed_at": now_iso},
                     params={"id": f"eq.{verification_id}"}, admin=True)

    supabase_request("PATCH", "agents",
                     data={"verified": True, "verification_status": "approved",
                           "verified_at": now_iso},
                     params={"phone": f"eq.{phone}"}, admin=True)

    supabase_request("PATCH", "listings",
                     data={"verified": True},
                     params={"phone": f"eq.{phone}"}, admin=True)

    flash(f'Agent {phone} has been verified!', 'success')
    return redirect('/admin/verifications')

# ── REJECT VERIFICATION ───────────────────────────────────────────
@app.route('/admin/reject-agent/<verification_id>/<phone>', methods=['POST'])
def verify_agent_reject(verification_id, phone):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')

    reason = request.form.get('rejection_reason', 'Documents could not be verified.')
    now_iso = datetime.now(timezone.utc).isoformat()

    supabase_request("PATCH", "verifications",
                     data={"verified": False, "status": "rejected",
                           "reviewed_at": now_iso,
                           "rejection_reason": reason},
                     params={"id": f"eq.{verification_id}"}, admin=True)

    supabase_request("PATCH", "agents",
                     data={"verified": False,
                           "verification_status": "rejected",
                           "rejection_reason": reason},
                     params={"phone": f"eq.{phone}"}, admin=True)

    flash(f'Verification rejected for {phone}.', 'warning')
    return redirect('/admin/verifications')

# ── ADMIN AMBASSADORS ─────────────────────────────────────────────
@app.route('/admin/ambassadors')
def admin_ambassadors():
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    response = supabase_request("GET", "ambassadors",
                                params={"order": "created_at.desc"}, admin=True)
    ambassadors = response.json() if response.status_code == 200 else []
    return render_template('admin_ambassadors.html', ambassadors=ambassadors)

# ── ADD AMBASSADOR ────────────────────────────────────────────────
@app.route('/admin/ambassadors/add', methods=['POST'])
def add_ambassador():
    if not session.get('admin'):
        return redirect('/futanest-control-2025')

    image_url = ''
    image_file = request.files.get('profile_image')
    if image_file and image_file.filename != '':
        image_upload = cloudinary.uploader.upload(
            image_file, folder="rentiva/ambassadors")
        image_url = image_upload.get('secure_url', '')

    data = {
        "full_name": request.form.get('full_name'),
        "political_position": request.form.get('political_position'),
        "department": request.form.get('department') or None,
        "faculty": request.form.get('faculty') or None,
        "profile_image_url": image_url,
        "is_active": True
    }

    response = supabase_request("POST", "ambassadors", data=data, admin=True)
    if response.status_code == 201:
        flash('Ambassador added successfully!', 'success')
    else:
        flash(f'Error adding ambassador: {response.text}', 'danger')

    return redirect('/admin/ambassadors')

# ── DEACTIVATE AMBASSADOR ─────────────────────────────────────────
@app.route('/admin/ambassadors/deactivate/<ambassador_id>')
def deactivate_ambassador(ambassador_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "ambassadors",
                     data={"is_active": False},
                     params={"id": f"eq.{ambassador_id}"}, admin=True)
    flash('Ambassador deactivated!', 'success')
    return redirect('/admin/ambassadors')

# ── ACTIVATE AMBASSADOR ───────────────────────────────────────────
@app.route('/admin/ambassadors/activate/<ambassador_id>')
def activate_ambassador(ambassador_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "ambassadors",
                     data={"is_active": True},
                     params={"id": f"eq.{ambassador_id}"}, admin=True)
    flash('Ambassador reactivated!', 'success')
    return redirect('/admin/ambassadors')

# ── DELETE AMBASSADOR ─────────────────────────────────────────────
@app.route('/admin/ambassadors/delete/<ambassador_id>')
def delete_ambassador(ambassador_id):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("DELETE", "ambassadors",
                     params={"id": f"eq.{ambassador_id}"}, admin=True)
    flash('Ambassador deleted!', 'success')
    return redirect('/admin/ambassadors')

# ── ADMIN LOGOUT ─────────────────────────────────────────────────
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)
