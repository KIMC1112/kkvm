#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "$0")"
if [[ ${1:-} != --inside ]]; then
  [[ $EUID == 0 ]] || exit 1
  export VM_NET_PARENT_MNT=$(readlink /proc/self/ns/mnt)
  export VM_NET_PARENT_NET=$(readlink /proc/self/ns/net)
  exec unshare --mount --net --propagation private bash "$0" --inside
fi
test "$(readlink /proc/self/ns/mnt)" != "$VM_NET_PARENT_MNT"
test "$(readlink /proc/self/ns/net)" != "$VM_NET_PARENT_NET"
# All mounts, addresses, rules, namespaces below are private. No real libvirt socket.
mount -t tmpfs tmpfs /run
mount -t tmpfs tmpfs /var/lib
mkdir -p /var/lib/vm_net /run/netns /run/vm-net-test
export VM_NET_TEST_ROOT=/run/vm-net-test
cp fixtures/domain.xml "$VM_NET_TEST_ROOT/domain.xml"
cp fixtures/network.xml "$VM_NET_TEST_ROOT/network.xml"
printf running > "$VM_NET_TEST_ROOT/vm-state"
touch "$VM_NET_TEST_ROOT/active"
chmod 755 mock-virsh.py
mount --bind "$PWD/mock-virsh.py" /usr/bin/virsh
python3 test-isolated.py
