"""Accept a GUI smoke report only when this run produced it for the expected frontend.

A leftover result.json from an earlier build, a report from the archived QML
launcher, or a zero exit code alone must never satisfy native acceptance.
"""
import argparse
import json
from pathlib import Path

NATIVE_FRONTEND = 'WinUI 3'


def verify_smoke_report(report_path, frontend=NATIVE_FRONTEND):
    """Return the report, or raise ValueError explaining why it cannot be used."""
    path = Path(report_path)
    if not path.is_file():
        raise ValueError(f'GUI smoke produced no report: {path}')
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError(f'GUI smoke report is unreadable: {path}: {error}') from error
    if not isinstance(report, dict) or report.get('success') is not True:
        message = report.get('message') if isinstance(report, dict) else None
        raise ValueError(message or f'GUI smoke did not succeed: {path}')
    if frontend:
        actual = report.get('frontend')
        if actual != frontend:
            raise ValueError(
                f'GUI smoke report frontend is {actual!r}, expected {frontend!r}: {path}')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--frontend', default=NATIVE_FRONTEND,
                        help='required frontend marker; empty accepts any report')
    args = parser.parse_args()
    try:
        report = verify_smoke_report(args.report, args.frontend)
    except ValueError as error:
        # A CI step reads the exit code, so report the reason without a traceback.
        raise SystemExit(str(error))
    print(report.get('message') or 'GUI smoke report accepted')


if __name__ == '__main__':
    main()
