import sys

if __name__ == "__main__":
    if "--worker" in sys.argv:
        from chaoxing_desktop.worker import main
        raise SystemExit(main())
    try:
        from chaoxing_desktop.qml_app import main
        raise SystemExit(main())
    except Exception as exc:
        # Windowed PyInstaller otherwise opens a blocking traceback dialog,
        # which prevents the automated offline acceptance from reporting errors.
        if "--smoke-test" not in sys.argv:
            raise
        import json
        from pathlib import Path
        destination = Path(sys.argv[sys.argv.index("--smoke-test") + 1]).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "result.json").write_text(json.dumps({"success": False, "message": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(1)
