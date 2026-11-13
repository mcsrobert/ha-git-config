import hashlib
import http.server
import io
import json
from pathlib import Path
import subprocess
import threading
import zipfile


class Server:
    """Serves fixed responses and records the headers of each request."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.requests.append((self.path, dict(self.headers)))
                if self.path not in server.routes:
                    self.send_error(404)
                    return
                body = server.routes[self.path]
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.httpd.server_port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def release(self, repository, tag, assets, digests=None):
        """Publish a release whose assets are served with their real digests unless overridden."""
        digests = digests or {}
        listing = []
        for name, data in assets.items():
            self.routes[f'/download/{repository}/{tag}/{name}'] = data
            listing.append({'name': name, 'browser_download_url': f'{self.url}/download/{repository}/{tag}/{name}',
                            'digest': digests.get(name, 'sha256:' + hashlib.sha256(data).hexdigest())})
        self.routes[f'/repos/{repository}/releases/tags/{tag}'] = json.dumps({'assets': listing}).encode()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in (files.items() if isinstance(files, dict) else files):
            archive.writestr(name, data)
    return buffer.getvalue()


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def repository(root, name, files, tag, annotated=False, symlinks=None):
    """Create a Git repository under root/name with one commit and a tag; returns the commit."""
    path = Path(root, name)
    path.mkdir(parents=True)
    git(path, 'init', '-q', '-b', 'main')
    for file, data in files.items():
        (path / file).parent.mkdir(parents=True, exist_ok=True)
        (path / file).write_text(data)
    for link, target in (symlinks or {}).items():
        (path / link).parent.mkdir(parents=True, exist_ok=True)
        (path / link).symlink_to(target)
    git(path, 'add', '-A')
    git(path, '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-q', '-m', 'init')
    if annotated:
        git(path, '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'tag', '-a', tag, '-m', tag)
    else:
        git(path, 'tag', tag)
    return git(path, 'rev-parse', 'HEAD')
