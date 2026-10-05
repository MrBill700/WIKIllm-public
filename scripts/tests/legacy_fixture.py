"""Load sanitized compatibility fixtures without private Git history."""
from pathlib import Path
from subprocess import CompletedProcess

VERSIONS = {
    '360cbcb': 'pre-alias-review',
    'c376aaa': 'pre-alias',
    '1c48b84': 'in-vault-state',
    'c13763d': 'pre-edit-deny',
    '4d11a71': 'pre-sync-sweep',
}

def legacy_result(spec, *, text=False, errors='strict', check=False):
    revision, relative = spec.split(':', 1)
    root = Path(__file__).resolve().parent / 'legacy-fixtures' / VERSIONS[revision]
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Fixture path must stay within its version directory')
    data = target.read_bytes()  # Missing fixtures fail, never silently skip coverage.
    return CompletedProcess(['legacy-fixture', spec], 0,
                            data.decode('utf-8', errors=errors) if text else data,
                            '' if text else b'')
