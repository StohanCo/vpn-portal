import os
import subprocess
import json
from flask import Flask, render_template, request, send_file, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from io import BytesIO
import secrets
from pathlib import Path

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
scheduler = BackgroundScheduler()
scheduler.start()

# WireGuard settings
WG_INTERFACE = "wg0"
WG_CMD = "/usr/bin/wg"
WG_SUBNET = "10.10.0."
WG_SERVER_PUBLIC_KEY = "/01kW6SfMYmQdVtSsnG4vZK8mcg7mDLP3vlcwnD2flY="
WG_ENDPOINT = "51.79.251.75:51820"
CLIENT_START_IP = 2  # 10.10.0.2

# Directory for temporary config files
TEMP_DIR = Path("/tmp/vpn-configs")
TEMP_DIR.mkdir(exist_ok=True)

def generate_keys():
    """Generate WireGuard private and public key pair."""
    client_private = subprocess.check_output([WG_CMD, "genkey"]).decode().strip()
    client_public = subprocess.check_output([WG_CMD, "pubkey"], input=client_private.encode()).decode().strip()
    return client_private, client_public

def save_temp_config(config_id, config_content, filename):
    """Save config to temporary file."""
    config_file = TEMP_DIR / f"{config_id}.json"
    with open(config_file, 'w') as f:
        json.dump({
            'content': config_content,
            'filename': filename,
            'created_at': datetime.now().isoformat()
        }, f)

def load_temp_config(config_id):
    """Load config from temporary file."""
    config_file = TEMP_DIR / f"{config_id}.json"
    if not config_file.exists():
        return None
    
    with open(config_file, 'r') as f:
        return json.load(f)

def delete_temp_config(config_id):
    """Delete temporary config file."""
    config_file = TEMP_DIR / f"{config_id}.json"
    if config_file.exists():
        config_file.unlink()

@app.route("/", methods=["GET", "POST"])
def index():
    config_content = None
    filename = None
    message = None
    config_id = None
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        duration = request.form.get("duration", "").strip()
        
        if not username:
            message = "Please enter your name."
            return render_template("index.html", message=message)
        
        try:
            duration_int = int(duration)
        except ValueError:
            message = "Invalid duration."
            return render_template("index.html", message=message)
        
        # Increment CLIENT_START_IP for next user
        global CLIENT_START_IP
        
        # Generate keys
        client_private, client_public = generate_keys()
        
        # Assign IP address
        client_ip = f"{WG_SUBNET}{CLIENT_START_IP}"
        CLIENT_START_IP += 1
        
        # Create WireGuard config
        config_content = f"""[Interface]
PrivateKey = {client_private}
Address = {client_ip}/24
DNS = 1.1.1.1

[Peer]
PublicKey = {WG_SERVER_PUBLIC_KEY}
Endpoint = {WG_ENDPOINT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"""
        
        # Add peer to WireGuard interface
        subprocess.run([
            WG_CMD, "set", WG_INTERFACE,
            "peer", client_public,
            "allowed-ips", f"{client_ip}/32"
        ])
        
        # Schedule peer removal after duration
        def remove_peer(pubkey):
            subprocess.run([WG_CMD, "set", WG_INTERFACE, "peer", pubkey, "remove"])
        
        scheduler.add_job(
            remove_peer, 
            'date', 
            run_date=datetime.now() + timedelta(minutes=duration_int), 
            args=[client_public]
        )
        
        # Generate filename with IP last digit
        ip_last_digit = client_ip.split('.')[-1]
        filename = f"SimpleVpnWireGuard{ip_last_digit}.conf"
        
        # Store config in file system
        config_id = secrets.token_urlsafe(16)
        save_temp_config(config_id, config_content, filename)
        
        # Schedule cleanup of temp file after 1 hour
        scheduler.add_job(
            delete_temp_config,
            'date',
            run_date=datetime.now() + timedelta(hours=1),
            args=[config_id]
        )
    
    return render_template("index.html", config=config_content, filename=filename, message=message, config_id=config_id)

@app.route("/download/<config_id>/<filename>")
def download(config_id, filename):
    config_data = load_temp_config(config_id)
    
    if not config_data:
        return "Config not found or expired", 404
    
    # Clean up after download
    delete_temp_config(config_id)
    
    return send_file(
        BytesIO(config_data['content'].encode()), 
        download_name=filename, 
        as_attachment=True,
        mimetype='text/plain'
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
