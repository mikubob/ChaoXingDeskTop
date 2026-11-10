"""Console-only frozen backend companion for the native WinUI application."""
import sys

if __name__ == "__main__":
    if "--bridge" in sys.argv:
        from chaoxing_desktop.winui_bridge import main
    elif "--worker" in sys.argv:
        from chaoxing_desktop.worker import main
    else:
        raise SystemExit("This component must be started by ChaoxingDesktop.")
    raise SystemExit(main())
