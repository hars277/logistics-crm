#!/usr/bin/env bash
# ============================================================================
#  Logistics CRM — one-shot VPS deploy (Ubuntu 22.04 / 24.04)
#  Hosts the Flask app + PostgreSQL + Nginx + free HTTPS on ONE cheap server.
#
#  HOW TO USE (on the VPS, as root):
#    1) Edit the 4 values below.
#    2) Put the app code in /opt/logistics_crm   (git clone OR upload).
#    3) chmod +x deploy_vps.sh && ./deploy_vps.sh
# ============================================================================
set -euo pipefail

# ---------------------- EDIT THESE 4 VALUES ----------------------
DOMAIN="crm.yourdomain.com"          # your purchased domain / subdomain
DB_PASS="ChangeThisDbPass123"        # PostgreSQL password (any strong text)
ADMIN_PASS="ChangeThisAdminPass123"  # login password for user 'admin'
EMAIL="you@example.com"              # for the free SSL certificate
# -----------------------------------------------------------------

APP_DIR="/opt/logistics_crm"
DB_NAME="logistics_crm"
DB_USER="crm_user"

echo ">> Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip postgresql postgresql-contrib nginx git curl ufw

echo ">> Setting up PostgreSQL database and user..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  ELSE
    ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='${DB_NAME}')\gexec
SQL

echo ">> Creating Python virtualenv and installing requirements..."
cd "${APP_DIR}"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo ">> Writing .env ..."
SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
cat > "${APP_DIR}/.env" <<ENV
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}
CRM_SECRET_KEY=${SECRET}
ADMIN_PASSWORD=${ADMIN_PASS}
COOKIE_SECURE=1
PORT=8000
ENV
chmod 600 "${APP_DIR}/.env"

echo ">> Creating database tables + seed data..."
cd "${APP_DIR}" && ./.venv/bin/python -c "import app; app.init_db()"

echo ">> Creating gunicorn systemd service..."
cat > /etc/systemd/system/logistics-crm.service <<UNIT
[Unit]
Description=Logistics CRM (gunicorn)
After=network.target postgresql.service

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn wsgi:app --bind 127.0.0.1:8000 --workers 3 --timeout 120
Restart=always
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
UNIT

chown -R www-data:www-data "${APP_DIR}"
systemctl daemon-reload
systemctl enable --now logistics-crm

echo ">> Configuring Nginx reverse proxy for ${DOMAIN}..."
cat > /etc/nginx/sites-available/logistics-crm <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};
    client_max_body_size 25M;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/logistics-crm /etc/nginx/sites-enabled/logistics-crm
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo ">> Firewall..."
ufw allow OpenSSH || true
ufw allow 'Nginx Full' || true
ufw --force enable || true

echo ">> Free HTTPS certificate (Let's Encrypt)..."
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}" --redirect || \
  echo "!! SSL step skipped — make sure ${DOMAIN}'s DNS A-record points to this server, then run: certbot --nginx -d ${DOMAIN}"

echo ""
echo "============================================================"
echo " DONE ✅  Open: https://${DOMAIN}"
echo " Login: admin / ${ADMIN_PASS}"
echo " Update later:  cd ${APP_DIR} && git pull && systemctl restart logistics-crm"
echo "============================================================"
