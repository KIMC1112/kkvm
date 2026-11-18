#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo bash install.sh'; exit 1; }
cd -- "$(dirname -- "$0")"
binary=target/release/vm_net
[[ -f "$binary" ]] || binary=bin/vm_net
test -f "$binary"
test -x /usr/bin/nft
test -x /usr/bin/virsh
test -x /usr/bin/ip
backup=/var/lib/vm_net/install-$(date +%Y%m%d-%H%M%S)
install -d -m0700 "$backup"
for f in /usr/local/bin/vm_net /etc/systemd/system/vm_net.service /etc/systemd/system/vm_net-routing.service; do
  if [[ -e $f ]]; then cp -a "$f" "$backup/$(basename "$f")"; sha256sum "$f" >> "$backup/original.sha256"; fi
done
install -m0755 "$binary" /usr/local/bin/vm_net
install -m0644 vm_net.service /etc/systemd/system/vm_net.service
install -m0644 vm_net-routing.service /etc/systemd/system/vm_net-routing.service
systemctl daemon-reload
systemctl enable vm_net.service vm_net-routing.service
# No VM/network power operations. Existing saved filters only, if any.
systemctl restart vm_net.service
systemctl restart vm_net-routing.service
/usr/local/bin/vm_net --help
echo "Installed; backup $backup"
