"""Exercise the real PTY path, including input modes and accidental submission."""

import io
import os
import select
import threading
import time
import unittest
from unittest.mock import patch

from fusion_agent.input_editor import InputEditor, _cell_slice, _cells

if os.name == "posix":
    import fcntl
    import pty
    import struct
    import termios


class EditorPipeTests(unittest.TestCase):
    def test_pipe_is_plain_and_no_completion_is_accepted(self):
        output = io.StringIO()
        editor = InputEditor(lambda value: ["/models"], input_stream=io.StringIO("/mod\n"), output_stream=output)
        self.assertEqual(editor.read_line("agent> "), "/mod")
        self.assertEqual(output.getvalue(), "agent> ")
        self.assertEqual(editor.history, ["/mod"])
        with self.assertRaises(EOFError):
            editor.read_line()

    def test_dumb_terminal_uses_plain_readline(self):
        class Dumb(io.StringIO):
            def isatty(self):
                return True
        output = Dumb()
        with patch.dict(os.environ, {"TERM": "dumb"}):
            value = InputEditor(input_stream=Dumb("hello\n"), output_stream=output).read_line("agent> ")
        self.assertEqual(value, "hello")
        self.assertNotIn("\x1b", output.getvalue())

    def test_candidates_reject_terminal_controls_and_multiline(self):
        editor = InputEditor(lambda value: ["/mod\x1b]52;c;secret\aels", "/mod\n/quit", "/models"])
        with patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            self.assertEqual(editor._candidate("/mod", 4), "els")
            self.assertEqual(editor._candidate("/mod", 3), "")
            self.assertEqual(editor._candidate("", 0), "")

    def test_failed_callback_does_not_break_input(self):
        def fail(value):
            raise OSError("stale catalog")
        editor = InputEditor(fail)
        self.assertEqual(editor._candidate("/m", 2), "")

    def test_cjk_cell_clipping_preserves_width(self):
        self.assertEqual(_cells("ab中文"), 6)
        self.assertEqual(_cell_slice("ab中文", 0, 5), "ab中 ")
        self.assertEqual(_cell_slice("ab中文", 3, 6), " 文")


class _PtySession:
    def __init__(self, suggestions=None, history=(), columns=40):
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, columns, 0, 0))
        self.previous = termios.tcgetattr(self.slave)
        self.input = os.fdopen(os.dup(self.slave), "r", encoding="utf-8")
        self.output = os.fdopen(os.dup(self.slave), "w", encoding="utf-8", buffering=1)
        self.editor = InputEditor(suggestions, history, input_stream=self.input, output_stream=self.output)
        self.value = None
        self.error = None
        self.captured = bytearray()
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self):
        try:
            self.value = self.editor.read_line("agent> ")
        except BaseException as exc:
            self.error = exc

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 2
        while b"agent> " not in self.captured and time.monotonic() < deadline:
            self.drain(.02)
        if b"agent> " not in self.captured:
            raise AssertionError("PTY editor did not start")
        return self

    def drain(self, timeout=.03):
        while select.select([self.master], [], [], timeout)[0]:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.captured.extend(chunk)
            # Let the editor finish a burst of keystrokes before the test
            # simulates a distinct human keypress.
            timeout = .015

    def send(self, value):
        os.write(self.master, value.encode("utf-8") if isinstance(value, str) else value)
        self.drain(.04)

    def finish(self, value=b"\r"):
        self.send(value)
        self.thread.join(2)
        self.drain()
        if self.thread.is_alive():
            raise AssertionError("PTY editor did not finish")
        if termios.tcgetattr(self.slave) != self.previous:
            raise AssertionError("stdin terminal attributes were not restored")
        return self.value

    def __exit__(self, *args):
        if self.thread.is_alive():
            os.write(self.master, b"\x03")
            self.thread.join(2)
        self.input.close()
        self.output.close()
        os.close(self.master)
        os.close(self.slave)


@unittest.skipUnless(os.name == "posix", "POSIX terminal editor")
class EditorPtyTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"TERM": "xterm-256color"})
        self.environment.start()
        self.color = patch.dict(os.environ)
        self.color.start()
        os.environ.pop("NO_COLOR", None)

    def tearDown(self):
        self.color.stop()
        self.environment.stop()

    def test_tab_accepts_gray_suffix_but_requires_enter(self):
        with _PtySession(lambda text: ["/models"]) as session:
            session.send("/mod")
            session.send(b"\t")
            self.assertTrue(session.thread.is_alive())
            self.assertIn(b"\x1b[90mels\x1b[0m", session.captured)
            self.assertEqual(session.finish(), "/models")
            self.assertIsNone(session.error)
            self.assertIn(b"\x1b[?2004l", session.captured)

    def test_right_accepts_only_at_end_and_keeps_middle_edits(self):
        with _PtySession(lambda text: ["/models"]) as session:
            session.send("/mod")
            session.send(b"\x1b[D\x1b[C")
            self.assertTrue(session.thread.is_alive())
            session.send(b"\x1b[C")
            self.assertEqual(session.finish(), "/models")

    def test_up_down_restores_draft(self):
        with _PtySession(history=["first", "中文历史"]) as session:
            session.send("draft")
            session.send(b"\x1b[A\x1b[A\x1b[B\x1b[B")
            self.assertEqual(session.finish(), "draft")

    def test_history_can_be_edited_before_submission(self):
        with _PtySession(history=["旧任务"]) as session:
            session.send(b"\x1b[A\x01\x1b[3~")
            session.send("新")
            self.assertEqual(session.finish(), "新任务")

    def test_long_chinese_horizontal_edit_and_delete(self):
        original = "这是很长的中文输入" * 10
        with _PtySession(columns=20) as session:
            session.send(original)
            session.send(b"\x7f\x1b[D")
            session.send("好")
            self.assertEqual(session.finish(), original[:-2] + "好" + original[-2:-1])

    def test_combining_character_backspace_removes_displayed_character(self):
        with _PtySession() as session:
            session.send("a" + "e\u0301")
            session.send(b"\x7f")
            self.assertEqual(session.finish(), "a")

    def test_bracketed_multiline_paste_is_one_editable_input(self):
        with _PtySession() as session:
            session.send(b"\x1b[200~first\n/quit\n\x1b[201~")
            self.assertTrue(session.thread.is_alive())
            self.assertEqual(session.finish(), "first\n/quit\n")
            self.assertEqual(len(session.editor.history), 1)

    def test_legacy_multiline_burst_needs_explicit_enter(self):
        with _PtySession() as session:
            session.send(b"first\n/quit\n")
            self.assertTrue(session.thread.is_alive())
            self.assertEqual(session.finish(), "first\n/quit\n")

    def test_paste_cannot_inject_osc_terminal_commands(self):
        with _PtySession() as session:
            session.send(b"\x1b[200~safe\x1b]52;c;c2VjcmV0\a input\x1b[201~")
            self.assertEqual(session.finish(), "safe input")
            self.assertNotIn(b"52;c", session.captured)

    def test_ctrl_c_and_ctrl_d_restore_terminal(self):
        for key, error in ((b"\x03", KeyboardInterrupt), (b"\x04", EOFError)):
            with self.subTest(key=key), _PtySession() as session:
                session.finish(key)
                self.assertIsInstance(session.error, error)
                self.assertEqual(session.editor.history, [])

    def test_ctrl_d_in_middle_deletes_instead_of_submitting(self):
        with _PtySession() as session:
            session.send(b"abc\x01\x04")
            self.assertTrue(session.thread.is_alive())
            self.assertEqual(session.finish(), "bc")

    def test_render_failure_restores_terminal(self):
        with _PtySession() as session:
            def fail(*args, **kwargs):
                raise ValueError("output failed")
            session.editor._draw = fail
            session.finish(b"a")
            self.assertIsInstance(session.error, ValueError)

    def test_no_color_cannot_accept_invisible_suggestions(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}), _PtySession(lambda text: ["/models"]) as session:
            session.send("/mod")
            session.send(b"\t\x1b[C")
            self.assertTrue(session.thread.is_alive())
            self.assertEqual(session.finish(), "/mod")
            self.assertNotIn(b"\x1b[90m", session.captured)


if __name__ == "__main__":
    unittest.main()
