#!/usr/bin/env bash
# =============================================================
# Esportes da Sorte — EC2 Setup Script (Ubuntu 22.04 / t3.small)
# Run as root or with sudo on a fresh EC2 instance.
# =============================================================
set -euo pipefail

APP_DIR="/home/ubuntu/esportesdasorte/backend"
REPO="https://github.com/ThiagoVenturaV/esportesdasorteback.git"

echo "=== [1/7] Atualizando pacotes ==="
apt-get update -y && apt-get upgrade -y

echo "=== [2/7] Instalando dependências do sistema ==="
apt-get install -y python3.11 python3.11-venv python3-pip nginx git curl

echo "=== [3/7] Clonando repositório ==="
if [ ! -d "$APP_DIR" ]; then
    mkdir -p "$(dirname $APP_DIR)"
    git clone "$REPO" "$APP_DIR"
else
    cd "$APP_DIR" && git pull origin main
fi

echo "=== [4/7] Criando venv e instalando dependências Python ==="
cd "$APP_DIR"
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [5/7] Configurando Nginx ==="
cp deploy/nginx.conf /etc/nginx/sites-available/esportesdasorte
ln -sf /etc/nginx/sites-available/esportesdasorte /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "=== [6/7] Configurando serviço systemd ==="
cp deploy/esportesdasorte.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable esportesdasorte
mkdir -p /var/log/gunicorn

echo "=== [7/7] Iniciando serviço ==="
# Lembre-se de criar o arquivo .env antes de iniciar!
if [ -f "$APP_DIR/.env" ]; then
    systemctl start esportesdasorte
    echo "✓ Serviço iniciado com sucesso!"
else
    echo "⚠ ATENÇÃO: Crie o arquivo $APP_DIR/.env antes de iniciar."
    echo "  Copie de .env.example e preencha as variáveis."
    echo "  Depois execute: sudo systemctl start esportesdasorte"
fi

echo ""
echo "============================================"
echo "  Setup concluído!"
echo "  Health check: curl http://localhost/health"
echo "============================================"
