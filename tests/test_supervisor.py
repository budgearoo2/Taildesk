from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from taildesk import supervisor
from taildesk.supervisor import EXIT_NO_PASSWORD, HEALTH_FAILURES, RESTARTED_FLAG, WORKER_FLAG, Supervisor


class FakeProcess:
    """A worker that exits with `code` after `checks` health intervals; None never exits."""

    def __init__(self, code, checks=0):
        self.code, self.checks, self.pid, self.killed = code, checks, 1234, False

    def wait(self, timeout=None):
        if self.killed:
            return 1
        if self.code is not None and self.checks <= 0:
            return self.code
        self.checks -= 1
        raise subprocess.TimeoutExpired("worker", timeout)

    def kill(self):
        self.killed = True


class SupervisorTests(unittest.TestCase):
    def run_supervisor(self, processes, probe=lambda url: None):
        commands, restores, sleeps = [], [], []

        def popen(command, **kwargs):
            commands.append(command)
            return processes.pop(0)

        result = Supervisor(
            ["TailDesk.exe"], 8765, popen=popen, probe=probe,
            restore=lambda: restores.append(True), clock=lambda: 0, sleep=sleeps.append,
        ).run()
        return result, commands, restores, sleeps

    def test_quit_from_tray_stops_without_restarting(self):
        result, commands, restores, _ = self.run_supervisor([FakeProcess(0)])
        self.assertEqual(result, 0)
        self.assertEqual(len(commands), 1)
        self.assertEqual(restores, [])
        self.assertIn(WORKER_FLAG, commands[0])
        self.assertNotIn(RESTARTED_FLAG, commands[0])

    def test_cancelled_password_setup_is_not_retried(self):
        result, commands, _, _ = self.run_supervisor([FakeProcess(EXIT_NO_PASSWORD)])
        self.assertEqual(result, EXIT_NO_PASSWORD)
        self.assertEqual(len(commands), 1)

    def test_crash_restores_desktop_and_restarts_minimized_with_backoff(self):
        access_violation = 0xC0000005
        processes = [FakeProcess(access_violation), FakeProcess(1), FakeProcess(0)]
        result, commands, restores, sleeps = self.run_supervisor(processes)
        self.assertEqual(result, 0)
        self.assertEqual(len(commands), 3)
        self.assertEqual(len(restores), 2)
        self.assertEqual(sleeps, list(supervisor.RESTART_DELAYS[:2]))
        for command in commands[1:]:
            self.assertIn("--minimized", command)
            self.assertIn(RESTARTED_FLAG, command)

    def test_hung_worker_is_killed_after_it_had_answered(self):
        answers = iter([{"ok": True, "bind": None}])
        hung = FakeProcess(None)
        result, commands, restores, _ = self.run_supervisor(
            [hung, FakeProcess(0)], probe=lambda url: next(answers, None)
        )
        self.assertTrue(hung.killed)
        self.assertEqual(result, 0)
        self.assertEqual(len(commands), 2)
        self.assertEqual(len(restores), 1)

    def test_silent_worker_is_not_killed_before_it_first_answers(self):
        # First-run password setup and slow imports happen before the listener opens.
        starting = FakeProcess(0, checks=HEALTH_FAILURES * 3)
        result, commands, _, _ = self.run_supervisor([starting])
        self.assertFalse(starting.killed)
        self.assertEqual(len(commands), 1)
        self.assertEqual(result, 0)

    def test_dead_tailnet_listener_is_restarted_once_it_had_worked(self):
        remote_answers = iter([True])

        def probe(url):
            if url.startswith("http://127.0.0.1:"):
                return {"ok": True, "bind": "100.100.1.2"}
            return {"ok": True} if next(remote_answers, False) else None

        hung = FakeProcess(None)
        self.run_supervisor([hung, FakeProcess(0)], probe=probe)
        self.assertTrue(hung.killed)

    def test_unreachable_tailnet_address_alone_never_triggers_restarts(self):
        def probe(url):
            return {"ok": True, "bind": "100.100.1.2"} if url.startswith("http://127.0.0.1:") else None

        worker = FakeProcess(0, checks=HEALTH_FAILURES * 3)
        self.run_supervisor([worker], probe=probe)
        self.assertFalse(worker.killed)

    def test_worker_command_runs_source_or_frozen_program(self):
        command = supervisor.worker_command(["TailDesk.exe"], restarted=False)
        self.assertTrue(command[1].endswith("remote_desktop_connection.py"))
        self.assertEqual(command[2:], [WORKER_FLAG])
        with patch.object(supervisor.sys, "frozen", True, create=True):
            command = supervisor.worker_command(["TailDesk.exe", "--minimized"], restarted=False)
        self.assertEqual(command[1:], [WORKER_FLAG, "--minimized"])

    def test_exit_description_names_windows_status_codes(self):
        self.assertIn("0xC0000005", supervisor.describe_exit(0xC0000005))
        self.assertEqual(supervisor.describe_exit(None), "stopped responding")


if __name__ == "__main__":
    unittest.main()
