#!/usr/bin/env bash
# Siapkan VPS Ubuntu 22.04/24.04 baru. Jalankan SEKALI:
#   sebagai root                      : bash setup-server.sh            (membuat user "deploy")
#   sebagai user yang sudah ada (sudo): sudo NEWUSER=$USER bash setup-server.sh
# Hasil: user "deploy" (sudo + docker), Docker, firewall (22/80/443), swap 2 GB, zona waktu WIB.
# Disk besar? jalankan: SWAP_GB=4 bash setup-server.sh
set -euo pipefail
NEWUSER=${NEWUSER:-deploy}
SWAP_GB=${SWAP_GB:-2}          # disk kecil (20 GB): 2 GB cukup untuk RAM 8 GB

echo "==> Update sistem"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get upgrade -y
apt-get install -y ca-certificates curl git ufw unattended-upgrades fail2ban
timedatectl set-timezone Asia/Jakarta

echo "==> User $NEWUSER"
if ! id "$NEWUSER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$NEWUSER"
  usermod -aG sudo "$NEWUSER"
  echo "$NEWUSER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$NEWUSER
  # user baru: pakai SSH key yang sama dengan root
  if [ -f /root/.ssh/authorized_keys ]; then
    install -d -m 700 -o "$NEWUSER" -g "$NEWUSER" /home/$NEWUSER/.ssh
    install -m 600 -o "$NEWUSER" -g "$NEWUSER" /root/.ssh/authorized_keys /home/$NEWUSER/.ssh/authorized_keys
  fi
fi
# user yang sudah ada: SSH key-nya TIDAK diubah

echo "==> Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$NEWUSER"
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
systemctl restart docker

echo "==> Firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Swap ${SWAP_GB} GB (cadangan saat model dimuat)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l ${SWAP_GB}G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/99-swap.conf
fi

echo "==> Bersihkan cache paket"
apt-get autoremove -y && apt-get clean
journalctl --vacuum-size=100M || true

echo "==> Selesai. Keluar lalu login ulang (supaya grup docker aktif): ssh $NEWUSER@<IP-VPS>"
