#!/usr/bin/env python3
"""Stand-in for odoo-bin in prefork mode, for lifecycle tests.

* Prints Odoo-style log lines twice per second.
* Forks N workers into the same process group, like Odoo with workers > 0.
* SIGTERM: logs a graceful shutdown, stops its workers, exits after a short delay.
* --ignore-term: ignores SIGTERM, to test the SIGKILL escalation.
"""

import argparse
import logging
import os
import random
import signal
import sys
import time

MESSAGES = [
    ("INFO", "odoo.modules.loading", "loading 142 modules..."),
    ("INFO", "werkzeug", '127.0.0.1 - - "POST /web/dataset/call_kw/res.partner/web_search_read HTTP/1.1" 200 - 12 0.010 0.031'),
    ("INFO", "odoo.addons.base.models.ir_cron", "Job 'Mail: Email Queue Manager' (3) done"),
    ("WARNING", "odoo.addons.base.models.ir_ui_view", "Field 'state' used in attributes must be present in view"),
    ("DEBUG", "odoo.sql_db", "query: SELECT id FROM res_users WHERE active"),
]


def worker_loop(index: int) -> None:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    log = logging.getLogger(f"odoo.service.server.worker{index}")
    while True:
        time.sleep(2)
        log.info("worker %d (pid %d) alive", index, os.getpid())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--exit-after", type=float, default=0, help="exit with code 3 after N seconds")
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stdout,
        level=logging.DEBUG,
        format="%(asctime)s %(process)d %(levelname)s fake_db %(name)s: %(message)s",
    )
    log = logging.getLogger("odoo")
    log.info("Odoo version 19.0 (fake) pid=%d pgid=%d uid=%d", os.getpid(), os.getpgid(0), os.getuid())

    workers = []
    for i in range(args.workers):
        pid = os.fork()
        if pid == 0:
            worker_loop(i)
        workers.append(pid)
        log.info("Worker WorkerHTTP (%d) alive", pid)

    stopping = False

    def on_term(signum, _frame):
        nonlocal stopping
        if args.ignore_term:
            log.warning("ignoring signal %d", signum)
            return
        stopping = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    started = time.monotonic()
    while not stopping:
        level, logger, message = random.choice(MESSAGES)
        logging.getLogger(logger).log(getattr(logging, level), message)
        if args.exit_after and time.monotonic() - started > args.exit_after:
            log.error("exiting on purpose with code 3")
            return 3
        time.sleep(0.5)

    log.info("Initiating shutdown")
    for pid in workers:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for pid in workers:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    time.sleep(1)
    log.info("Hit CTRL-C again or send a second signal to force the shutdown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
