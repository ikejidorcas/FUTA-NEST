from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
import os
import requests
import cloudinary
import cloudinary.uploader

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
    if request.method == 'POST':
        image_url = ''
        video_url = ''
        cloudinary.config(
    cloud_name="da6gxwgjq",
    api_key="636751886513396",
    api_secret="5AZMUQGgtFo_A5c9cZZTmPlpJYo"
)

        # Handle image upload to Cloudinary
        image_file = request.files.get('image')
        if image_file and image_file.filename != '':
            image_upload = cloudinary.uploader.upload(
                image_file,
                folder="futa-nest/images"
            )
            image_url = image_upload.get('secure_url', '')

        # Handle video upload to Cloudinary
        video_file = request.files.get('video')
        if video_file and video_file.filename != '':
            video_upload = cloudinary.uploader.upload(
                video_file,
                resource_type="video",
                folder="futa-nest/videos"
            )
            video_url = video_upload.get('secure_url', '')

        data = {
            "agent_name": request.form.get('agent_name'),
            "phone": request.form.get('phone'),
            "title": request.form.get('title'),
            "description": request.form.get('description'),
            "area": request.form.get('area'),
            "rooms": int(request.form.get('rooms')),
            "price": int(request.form.get('price')),
            "image_url": image_url,
            "video_url": video_url,
            "approved": False
        }

        response = supabase_request("POST", "listings", data=data)
        if response.status_code == 201:
            flash('Listing submitted! It will appear after admin approval.', 'success')
        else:
            flash(f'Error: {response.text}', 'danger')
        return redirect('/post-listing')
    return render_template('post_listing.html')

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
    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"})
    return render_template('taken.html')

@app.route('/admin/taken/<listing_id>')
def admin_mark_taken(listing_id):
    if not session.get('admin'):
        return redirect('/admin')
    supabase_request("PATCH", "listings",
                     data={"available": False},
                     params={"id": f"eq.{listing_id}"})
    flash('Listing marked as taken!', 'success')
    return redirect('/admin/dashboard')

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)