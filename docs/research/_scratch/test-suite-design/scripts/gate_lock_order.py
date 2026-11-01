"""Which waiting gate gets the machine's gate lock next (#385): a holder has it, a worker gate starts waiting, then an
integrate gate. Prints how often integrate's gate wins in 20 rounds, without and with its priority.
Usage: uv run python gate_lock_order.py"""

import fcntl
import tempfile
import threading
import time
from pathlib import Path

from devkit import gate


def integrate_wins(priority: bool, rounds: int = 20) -> int:
    wins = 0
    for _ in range(rounds):
        path = Path(tempfile.mkdtemp()) / "gate.lock"
        with open(path, "a+") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            order: list[str] = []
            waiting = {"worker": threading.Event(), "integrate": threading.Event()}

            def run(name, path=path, order=order, waiting=waiting):
                with gate.gate_lock(path, on_wait=waiting[name].set, priority=priority and name == "integrate"):
                    order.append(name)
                    time.sleep(0.02)

            threads = {n: threading.Thread(target=run, args=(n,)) for n in waiting}
            for name in ("worker", "integrate"):
                threads[name].start()
                waiting[name].wait(5)
        for t in threads.values():  # the holder's file closed above: the lock is free
            t.join()
        wins += order[0] == "integrate"
    return wins


for priority in (False, True):
    print(f"priority={priority}: integrate's gate got the lock next in {integrate_wins(priority)}/20 rounds")
