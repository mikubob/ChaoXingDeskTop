"""Shareable diagnostics: only typed policy values, never arbitrary user text."""
from PySide6 import __version__ as pyside_version
import math
import platform

from . import __version__
from .config import parser
from .qml_schema import SCHEMA


def diagnostic_config(config):
    out = parser()
    out['diagnostics'] = dict(application=__version__, python=platform.python_version(),
                              pyside=pyside_version, scope='policy-only; no sessions or content')
    for groups in SCHEMA.values():
        for group in groups:
            for spec in group['fields']:
                section, key = spec['section'], spec['key']
                value = config.get(section, key, fallback='')
                kind = spec['kind']
                allowed = False
                if kind == 'bool':
                    value = value.lower()
                    allowed = value in ('true', 'false')
                elif kind == 'choice':
                    allowed = value in {c['value'] for c in spec['choices']}
                elif kind in ('integer', 'number'):
                    try:
                        number = int(value) if kind == 'integer' else float(value)
                        allowed = math.isfinite(number) and spec['low'] <= number <= spec['high']
                        value = str(number)
                    except ValueError:
                        pass
                if allowed:
                    if not out.has_section(section):
                        out.add_section(section)
                    out[section][key] = value
    from .providers import PROVIDER_SPECS
    if not out.has_section('question_bank'):
        out.add_section('question_bank')
    out['question_bank']['providers'] = ','.join(
        name for name in config['question_bank']['providers'].split(',') if name in PROVIDER_SPECS)
    import io
    stream = io.StringIO()
    out.write(stream)
    return stream.getvalue()
