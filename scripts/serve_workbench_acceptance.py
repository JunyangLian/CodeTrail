"""Disposable browser-QA server with scripted responses and an isolated fixture."""

import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pico.providers.clients import FakeModelClient
from pico.runtime import Pico, SessionStore
from pico.web import Workbench, make_server
from pico.workspace import WorkspaceContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="codetrail-browser-qa-") as directory:
        root = Path(directory)
        (root / "note.txt").write_text("original\n", encoding="utf-8")
        (root / "test_note.py").write_text(
            "import unittest\nfrom pathlib import Path\nclass NoteTests(unittest.TestCase):\n"
            "    def test_note(self): self.assertEqual(Path('note.txt').read_text().strip(), 'approved')\n", encoding="utf-8")

        class TimeoutClient(FakeModelClient):
            def complete(self, *args, **kwargs):
                raise TimeoutError("Browser QA simulated model timeout")

        def factory(session_id, policy, max_steps):
            task = app.tasks[app.active_id]
            if task.prompt.startswith("[allow]"):
                client = FakeModelClient([
                    '<tool>{"name":"patch_file","args":{"path":"note.txt","old_text":"original","new_text":"approved"}}</tool>',
                    '<tool>{"name":"run_shell","args":{"command":"python -B -m unittest discover -v"}}</tool>',
                    '<final>Approved write and verification completed.</final>',
                ])
            elif task.prompt.startswith(("[deny]", "[readonly]")):
                client = FakeModelClient([
                    '<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"must not exist"}}</tool>',
                    '<final>The write was blocked.</final>',
                ])
            elif task.prompt.startswith("[timeout]"):
                client = TimeoutClient([])
            elif task.prompt.startswith("[budget]"):
                client = FakeModelClient(['<tool>{"name":"read_file","args":{"path":"note.txt"}}</tool>'] * 3)
            else:
                client = FakeModelClient(['<final>Continued existing session.</final>'])
            options = dict(model_client=client, workspace=WorkspaceContext.build(root, repo_root_override=root),
                           session_store=SessionStore(root / ".pico/sessions"), approval_policy=policy,
                           read_only=policy == "never", max_steps=max_steps)
            return Pico.from_session(session_id=session_id, **options) if session_id else Pico(**options)

        app = Workbench(root, factory=factory, verification_command="python -B -m unittest discover -v")
        app.model = FakeModelClient([])
        app.model.model = "SCRIPTED BROWSER QA · isolated fixture"
        server = make_server(app, args.port)
        print(f"Scripted browser QA: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
