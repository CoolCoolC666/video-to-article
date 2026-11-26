# -*- coding: utf-8 -*-
"""PyInstaller entry for the desktop GUI.

Also supports a headless CLI mode so the green package can be driven from a
shell on machines that have no Python:

    YilanChengWen.exe --cli --url <link> --prompts general_article

The GUI is the only entrypoint otherwise. The CLI run also lands in
logs/cli_run.log, because a windowed exe has no console of its own.
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from video_to_article.paths import LOGS_DIR

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = open(LOGS_DIR / "cli_run.log", "w", encoding="utf-8", errors="replace")
        console = sys.stdout

        class _Tee:
            """print() 同时写到 stdout（如果有）和日志文件。"""

            def __init__(self):
                self._log = log_file
                self._out = console

            def write(self, s):
                try:
                    self._log.write(s)
                except Exception:
                    pass
                if self._out is not None:
                    try:
                        self._out.write(s)
                    except Exception:
                        pass
                return len(s)

            def flush(self):
                for target in (self._out, self._log):
                    try:
                        target.flush()
                    except Exception:
                        pass

        sys.stdout = _Tee()
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        try:
            from video_to_article.cli import main as cli_main

            cli_main()
        finally:
            try:
                sys.stdout.flush()
            except Exception:
                pass
            log_file.close()
        raise SystemExit(0)

    from video_to_article.gui.app import main

    main()
