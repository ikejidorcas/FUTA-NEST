from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import requests
import cloudinary
import cloudinary.uploader


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

@app.route('/post-listing', methods=['GET', 'POST'])
def post_listing():

    if request.method == 'POST':
        image_url = ''
        video_url = ''

        # Get form data directly (no session)
        agent_name = request.form.get('agent_name')
        phone = request.form.get('phone')

        # Optional: basic validation
        if not agent_name or not phone:
            flash('Name and phone number are required.', 'danger')
            return redirect('/post-listing')

        # Check if agent is blocked (optional safety)
        blocked_check = supabase_request(
            "GET",
            "agents",
            params={
                "phone": f"eq.{phone}",
                "blocked": "eq.true"
            }
        )

        if blocked_check.json():
            flash('Your account has been blocked. Contact admin.', 'danger')
            return redirect('/')

        # Upload image
        image_file = request.files.get('image')
        if image_file and image_file.filename != '':
            image_upload = cloudinary.uploader.upload(
                image_file,
                folder="futa-nest/images"
            )
            image_url = image_upload.get('secure_url', '')

        # Upload video
        video_file = request.files.get('video')
        if video_file and video_file.filename != '':
            video_upload = cloudinary.uploader.upload(
                video_file,
                resource_type="video",
                folder="futa-nest/videos"
            )
            video_url = video_upload.get('secure_url', '')

        # Prepare data
        data = {
            "agent_name": agent_name,
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

        # Save to database
        response = supabase_request("POST", "listings", data=data)

        if response.status_code == 201:
            flash('Listing submitted! It will appear after admin approval.', 'success')
        else:
            flash(f'Error: {response.text}', 'danger')

        return redirect('/post-listing')

    return render_template('post_listing.html')

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





# ── ADMIN VERIFICATIONS ───────────────────────────────────────────
@app.route('/admin/verifications')
def admin_verifications():
    if not session.get('admin'):
        return redirect('/admin')
    response = supabase_request("GET", "verifications",
                                params={"order": "created_at.desc"})
    verifications = response.json() if response.status_code == 200 else []
    return render_template('admin_verifications.html', verifications=verifications)



# ── ADMIN LOGOUT ─────────────────────────────────────────────────
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)