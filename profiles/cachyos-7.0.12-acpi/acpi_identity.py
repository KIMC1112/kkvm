#!/usr/bin/env python3
"""Apply the identity-only ACPI overlay; never import table bodies or DMA flags."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import struct

SIGNATURES = ('APIC', 'DSDT', 'FACP', 'HPET', 'IVRS', 'MCFG', 'TPM2')
MARKER = 'kkvm_host_acpi_identity'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def read_identity(directory):
    result = {}
    for sig in SIGNATURES:
        p = Path(directory) / sig
        if not p.exists():
            continue
        data = p.read_bytes()
        if len(data) < 36 or data[:4] != sig.encode():
            raise ValueError('Invalid ACPI header: ' + sig)
        length = struct.unpack_from('<I', data, 4)[0]
        if length != len(data) or sum(data) % 256:
            raise ValueError('Invalid ACPI length/checksum: ' + sig)
        result[sig] = data[10:36].hex()
    if not {'FACP', 'IVRS'}.issubset(result):
        raise ValueError('This AMD profile requires host FACP and IVRS tables')
    return result

def validate_identity(identity):
    if not {'FACP', 'IVRS'}.issubset(identity):
        raise ValueError('FACP and IVRS identities are required')
    for sig, value in identity.items():
        if sig not in SIGNATURES or not isinstance(value, str) or len(bytes.fromhex(value)) != 26:
            raise ValueError('Invalid identity: ' + sig)

def overlay(aml, acpi, identity):
    validate_identity(identity)
    if MARKER in aml:
        raise ValueError('Already patched: use a fresh source copy')
    start = 'void acpi_table_begin('
    needle = '    build_append_int_noprefix(array, 1, 4);                       /* Creator Revision */\n'
    revision = 'AcpiTable table = { .sig = "IVRS", .rev = 1, .oem_id = oem_id,'
    if aml.count(start) != 1 or aml.count(needle) != 1 or acpi.count(revision) != 1:
        raise ValueError('Unexpected QEMU source; refusing a partial patch')
    rows = []
    for sig in SIGNATURES:
        if sig in identity:
            rows.append('    { "' + sig + '", { ' + ', '.join('0x%02x' % x for x in bytes.fromhex(identity[sig])) + ' } },')
    header = '''/* Host-derived identity bytes only. Never import host MMIO, topology,
 * table lengths, feature flags, or firmware protection assertions. */
static const struct {
    char signature[5];
    uint8_t identity[26];
} kkvm_host_acpi_identity[] = {
''' + '\n'.join(rows) + '\n};\n\n'
    extra = '''    /* Override header identity before acpi_table_end computes checksum. */
    for (size_t i = 0; i < G_N_ELEMENTS(kkvm_host_acpi_identity); i++) {
        if (!memcmp(desc->sig, kkvm_host_acpi_identity[i].signature, 4)) {
            memcpy(array->data + desc->table_offset + 10,
                   kkvm_host_acpi_identity[i].identity, 26);
            break;
        }
    }
'''
    return (aml.replace(start, header + start).replace(needle, needle + extra),
            acpi.replace(revision, revision.replace('.rev = 1', '.rev = 2')))

def apply(source, identity, output):
    root = Path(source).resolve()
    dest = Path(output).resolve()
    if dest.exists():
        raise ValueError('Evidence directory exists; refusing to overwrite')
    paths = ('hw/acpi/aml-build.c', 'hw/i386/acpi-build.c')
    old = [(root / p).read_bytes() for p in paths]
    new = overlay(*(b.decode() for b in old), identity)
    # Validate both inputs before writing either. Backups always precede edits.
    dest.mkdir(parents=True)
    manifest = {}
    patch = []
    for p, before, after in zip(paths, old, new):
        (dest / (Path(p).name + '.before')).write_bytes(before)
        manifest[p] = {'before': sha(before), 'after': sha(after.encode())}
        patch.extend(difflib.unified_diff(before.decode().splitlines(True), after.splitlines(True),
                                         fromfile='a/' + p, tofile='b/' + p))
    (dest / 'identity-only.patch').write_text(''.join(patch), encoding='utf-8')
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for p, after in zip(paths, new):
        (root / p).write_bytes(after.encode())
    return manifest

def restore(source, output):
    root, dest = Path(source).resolve(), Path(output).resolve()
    manifest = json.loads((dest / 'manifest.json').read_text(encoding='utf-8'))
    expected = {'hw/acpi/aml-build.c', 'hw/i386/acpi-build.c'}
    if set(manifest) != expected:
        raise ValueError('Unexpected backup manifest')
    originals = {}
    for p, hashes in manifest.items():
        data = (dest / (Path(p).name + '.before')).read_bytes()
        if sha(data) != hashes['before'] or sha((root / p).read_bytes()) not in (hashes['before'], hashes['after']):
            raise ValueError('Changed source/backup: refusing restore for ' + p)
        originals[p] = data
    for p, data in originals.items():
        (root / p).write_bytes(data)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('capture')
    c.add_argument('--tables', default='/sys/firmware/acpi/tables')
    for command in ('apply', 'restore'):
        p = sub.add_parser(command)
        p.add_argument('--source', required=True)
        p.add_argument('--evidence', required=True)
        if command == 'apply':
            p.add_argument('--identity', required=True)
    a = parser.parse_args()
    if a.command == 'capture':
        print(json.dumps(read_identity(a.tables), indent=2))
    elif a.command == 'apply':
        print(json.dumps(apply(a.source, json.loads(Path(a.identity).read_text(encoding='utf-8')), a.evidence), indent=2))
    else:
        restore(a.source, a.evidence)
        print('Source backups restored; installed binaries and VMs were not touched.')

if __name__ == '__main__':
    main()
