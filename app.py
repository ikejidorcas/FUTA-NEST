from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import requests
import cloudinary
import cloudinary.uploader
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-later')

# Rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["2000 per day", "200 per hour"]
)

# Supabase config
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')

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

# ── POST LISTING ─────────────────────────────────────────────────
@app.route('/post-listing', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
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

        # Check if agent is blocked
        blocked_check = supabase_request("GET", "agents",
                                         params={"phone": f"eq.{phone}",
                                                 "blocked": "eq.true"})
        if blocked_check.json():
            flash('Your number has been blocked from Rentiva. Contact admin if this is a mistake.', 'danger')
            return redirect('/')

        # Check listing limit
        existing_listings = supabase_request("GET", "listings",
                                             params={"phone": f"eq.{phone}",
                                                     "available": "eq.true"})
        if existing_listings.json() and len(existing_listings.json()) >= 3:
            flash('You already have 3 active listings. Please mark a house as taken before posting a new one.', 'danger')
            return redirect('/post-listing')

        price = int(request.form.get('price', 0))

        if price < 30000:
            flash('Price seems too low. Minimum price is ₦30,000/year.', 'danger')
            return redirect('/post-listing')
        if price > 2000000:
            flash('Price seems unusually high. Please contact admin if this is correct.', 'danger')
            return redirect('/post-listing')

        # Check for duplicate
        duplicate_check = supabase_request("GET", "listings",
                                           params={"phone": f"eq.{phone}",
                                                   "area": f"eq.{request.form.get('area')}",
                                                   "price": f"eq.{price}",
                                                   "available": "eq.true"})
        if duplicate_check.json():
            flash('You already have a listing with the same area and price.', 'danger')
            return redirect('/post-listing')

        flagged = price < 50000 or price > 1000000

        # Upload image
        image_file = request.files.get('image')
        if image_file and image_file.filename != '':
            image_upload = cloudinary.uploader.upload(
                image_file, folder="futa-nest/images")
            image_url = image_upload.get('secure_url', '')

        # Upload video
        video_file = request.files.get('video')
        if video_file and video_file.filename != '':
            video_upload = cloudinary.uploader.upload(
                video_file, resource_type="video", folder="futa-nest/videos")
            video_url = video_upload.get('secure_url', '')

        # Register agent if not existing
        existing_agent = supabase_request("GET", "agents",
                                          params={"phone": f"eq.{phone}"})
        if not existing_agent.json():
            supabase_request("POST", "agents", data={
                "phone": phone,
                "name": agent_name,
                "verified": False,
                "flagged": False,
                "blocked": False
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
                "blocked": False
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

    all_listings = all_response.json() if all_response.status_code == 200 else []
    pending = pending_response.json() if pending_response.status_code == 200 else []

    return render_template('admin_dashboard.html',
                           all_listings=all_listings,
                           pending=pending)

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
    response = supabase_request("GET", "verifications",
                                params={"order": "created_at.desc"}, admin=True)
    verifications = response.json() if response.status_code == 200 else []
    return render_template('admin_verifications.html', verifications=verifications)

# ── VERIFY AGENT APPROVE ──────────────────────────────────────────
@app.route('/admin/verify-agent/<verification_id>/<phone>')
def verify_agent_approve(verification_id, phone):
    if not session.get('admin'):
        return redirect('/futanest-control-2025')
    supabase_request("PATCH", "verifications",
                     data={"verified": True},
                     params={"id": f"eq.{verification_id}"}, admin=True)
    supabase_request("PATCH", "listings",
                     data={"verified": True},
                     params={"phone": f"eq.{phone}"}, admin=True)
    flash('Agent verified!', 'success')
    return redirect('/admin/verifications')

# ── ADMIN LOGOUT ─────────────────────────────────────────────────
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)