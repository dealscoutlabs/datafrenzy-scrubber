"""Entry point of the packaged desktop app (DataFrenzyScrubber.exe)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # spawned scrubbing workers re-enter here
    from scrubber.desktop.app import main
    sys.exit(main())
