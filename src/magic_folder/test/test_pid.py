# Copyright 2022 Least Authority TFA GmbH
# See COPYING for details.

from twisted.logger import (
    Logger,
)
from twisted.python.filepath import (
    FilePath,
)
from testtools.matchers import (
    Always,
    Equals,
    Contains,
    ContainsDict,
    AllMatch,
    HasLength,
)
from twisted.internet.testing import (
    EventLoggingObserver,
)

from .common import (
    SyncTestCase,
)
from ..pid import (
    check_pid_process,
)


class _FakeProcess:
    """
    Enough of psutil.Process to test check_pid_process
    """
    running = True

    def __init__(self, pid):
        self.pid = pid

    def create_time(self):
        return 123.4

    def terminate(self):
        self.running = False


class TestPidObserver(SyncTestCase):
    """
    Confirm operation of magic_folder.pid functions
    """

    def test_happy(self):
        """
        normal operation of pid-file writing
        """
        pidfile = FilePath(self.mktemp())
        log = Logger()
        with check_pid_process(pidfile, log, find_process=_FakeProcess):
            self.assertThat(
                pidfile.exists(),
                Equals(True),
            )
        self.assertThat(
            pidfile.exists(),
            Equals(False),
        )

    def test_not_running(self):
        """
        a pid-file refers to a non-running process
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"65537 1234.5")  # "impossible" process-id .. right?
        obs = EventLoggingObserver()
        log = Logger()
        log.observer = obs
        with check_pid_process(pidfile, log):
            pass

        events = list(obs)

        # both logged events should have a "pidpath" kwarg
        self.assertThat(events, HasLength(2))
        self.assertThat(
            events,
            AllMatch(
                ContainsDict({
                    "pidpath": Always(),
                }),
            )
        )

    def test_existing(self):
        """
        a pid-file refers to a running process so we should exit
        """
        pidfile = FilePath(self.mktemp())
        pidfile.setContent(b"0 0.0\n")
        obs = EventLoggingObserver()
        log = Logger()
        log.observer = obs

        with self.assertRaises(Exception) as ctx:
            with check_pid_process(pidfile, log, find_process=_FakeProcess):
                pass
        self.assertThat(
            str(ctx.exception),
            Contains("already running")
        )
