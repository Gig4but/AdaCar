"""Entry point: `adacar` opens the app window; `adacar --browser` serves the UI only.
Developer tool: `adacar probe [source ...]` checks that the portals can still be read."""

import argparse
import logging
import os
import sys
import webbrowser

from adacar import __version__, paths
from adacar.desktop import update
from adacar.desktop.instance import SingleInstance
from adacar.desktop.server import BackgroundServer, ServerStartError
from adacar.i18n import translate
from adacar.logs import report_error, setup_logging
from adacar.services import Services
from adacar.system.resources import keep_full_speed
from adacar.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["probe"]:
        from adacar.scraper.probe import main as probe_main

        return probe_main(argv[1:])
    parser = argparse.ArgumentParser(prog="adacar")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--browser", action="store_true", help="open the UI in the default browser, no window"
    )
    parser.add_argument("--port", type=int, help="fixed port for the local web server")
    parser.add_argument(
        "--minimized", action="store_true", help="start in the tray (start with Windows)"
    )
    args = parser.parse_args(argv)

    log_file = setup_logging()
    keep_full_speed()
    gui = not args.browser
    instance = SingleInstance()
    if not instance.acquire():  # already running: bring that one up instead of a second copy
        if instance.wake_running(open_browser=args.browser):
            return 0
        report_error("AdaCar už běží, ale neodpovídá. Zkuste to za chvíli znovu.", gui)
        return 1
    pending = paths.data_dir() / update.FOLDER
    installer = update.take_pending(pending) if update.installed() else None
    if installer and update.run_installer(*installer, minimized=args.minimized):
        instance.release()  # automatic updates: installed before the app starts
        return 0
    try:
        app = create_app()  # settings, database (migrated), services
    except Exception:
        logging.getLogger(__name__).exception("start-up failed")
        instance.release()
        report_error(f"AdaCar se nepodařilo spustit. Podrobnosti: {log_file}", gui)
        return 1
    instance.add_show_route(app)
    server = BackgroundServer(app, port=args.port)
    try:
        server.start()
    except ServerStartError:
        instance.release()
        report_error(f"AdaCar se nepodařilo spustit. Podrobnosti: {log_file}", gui)
        return 1
    instance.publish(server.url)
    svc = app.state.services
    try:
        if args.browser:
            svc.updates.quit = server.stop
            instance.on_show = lambda: webbrowser.open(server.url)
            webbrowser.open(server.url)
            print(f"AdaCar běží na {server.url} (ukončete Ctrl+C)")
            server.wait()
        else:
            from adacar.desktop.window import run_desktop

            automation = svc.settings.automation

            def on_tray(icon) -> None:
                svc.scheduler.notify = lambda title, text: icon.notify(text, title)
                svc.scheduler.on_change = icon.update_menu

            # The tray is only needed while automatic searches are on.
            run_desktop(
                server,
                controls=tray_controls(svc),
                instance=instance,
                keep_in_tray=lambda: automation.enabled,
                on_tray=on_tray,
                hidden=args.minimized and automation.enabled,
                on_quit=lambda quit_app: setattr(svc.updates, "quit", quit_app),
            )
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        instance.release()
    if svc.updates.installer:  # "Nainstalovat" in Settings: the app has quit for it
        update.run_installer(*svc.updates.installer)
    leave_search(app)
    return 0


def tray_controls(svc: Services):
    from adacar.desktop.tray import TrayControls

    settings, scheduler = svc.settings, svc.scheduler
    return TrayControls(
        text=lambda key: translate(settings.language, key),
        search=svc.runner.start,
        auto_on=lambda: settings.automation.enabled,
        set_auto=svc.set_automation,
        paused=scheduler.paused,
        pause=scheduler.pause,
        resume=scheduler.resume,
    )


def leave_search(app) -> None:
    """A search the shutdown could not stop (a long download): Python would wait at exit for
    its threads, keeping the process and the terminal busy for minutes. What it found so far
    is saved."""
    if app.state.services.runner.progress.running:
        logging.shutdown()
        os._exit(0)
