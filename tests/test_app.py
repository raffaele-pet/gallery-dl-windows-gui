import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
import engine


def png(size=(120, 100)):
    out = io.BytesIO()
    Image.new('RGB', size, 'purple').save(out, format='PNG')
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/image'):
            data, kind, code = png(), 'image/png', 200
        elif self.path == '/invalid':
            data, kind, code = b'not an image', 'image/jpeg', 200
        elif self.path == '/empty':
            data, kind, code = b'<h1>No images</h1>', 'text/html', 200
        elif self.path == '/partial':
            data, kind, code = b'<img src="/image.png"><img src="/invalid">', 'text/html', 200
        elif self.path == '/dynamic':
            data, kind, code = b'<script>setTimeout(()=>document.body.innerHTML="<img src=\'/image.png\'>",100)</script>', 'text/html', 200
        else:
            data, kind, code = b'<img srcset="/small.png 100w, /image.png 1000w"><img data-src="/image.png"><img src="/image-copy.png">', 'text/html', 200
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_markdown_regression(self):
        self.assertEqual(engine.normalize_urls('[https://www.repubblica.it/](https://www.repubblica.it/)'), ['https://www.repubblica.it/'])

    def test_automatic_folder_names(self):
        cases = {
            'https://www.repubblica.it/?ref=RHHD-L': 'www.repubblica.it',
            'https://www.repubblica.it/sport/?ref=RHHD-MS': 'www.repubblica.it-sport',
            'https://www.instagram.com/raffaele.pet/': 'www.instagram.com-raffaele.pet',
        }
        for url, expected in cases.items():
            self.assertEqual(engine.folder_name_from_url(url), expected)

    def test_normalization(self):
        self.assertEqual(engine.normalize_urls('<https://example.org/a?q=1&amp;x=2>\nhttps://example.org/a?q=1&x=2'), ['https://example.org/a?q=1&x=2'])
        self.assertEqual(engine.normalize_urls('www.repubblica.it'), ['https://www.repubblica.it/'])
        self.assertEqual(engine.normalize_urls('[Photo](https://example.org/a_(b).jpg)'), ['https://example.org/a_(b).jpg'])
        for text in ('', 'hello', 'file:///C:/file', 'https://user:pass@example.org'):
            with self.assertRaises(ValueError):
                engine.normalize_urls(text)

    def test_extraction(self):
        document = '<base href="https://example.org/photos/"><img srcset="small.jpg 1x, big.jpg 2x"><img data-src="lazy.png"><meta property="og:image" content="big.jpg"><script type="application/ld+json">{"image":{"url":"json.jpg"}}</script>'
        self.assertEqual(engine.image_urls(document, self.base), ['https://example.org/photos/big.jpg', 'https://example.org/photos/lazy.png', 'https://example.org/photos/json.jpg'])

    def test_image_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = engine.save_image(png(), self.base + '/CON.png', root)
            self.assertTrue(first[0].is_file())
            self.assertFalse(first[1])
            self.assertTrue(engine.save_image(png(), self.base + '/CON.png', root)[1])
            self.assertIsNone(engine.save_image(png((1, 1)), self.base, root))
            with self.assertRaises(OSError):
                engine.save_image(b'not image', self.base, root)

    def test_generic_download_and_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(engine.generic_download(self.base, Path(tmp)), (1, 0))
            self.assertEqual(len(list(Path(tmp).rglob('*.png'))), 1)
            self.assertEqual(engine.generic_download(self.base, Path(tmp)), (1, 0))
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertTrue(any(e.get('existing') for e in events))

    def test_partial_download_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(engine.generic_download(self.base + '/partial', Path(tmp)), (1, 1))
            self.assertEqual(engine.run({'urls': [self.base + '/partial'], 'destination': tmp}), 0)
            self.assertIn('elementi non validi ignorati', output.getvalue())

    def test_empty_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch('engine.rendered_images', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                engine.generic_download(self.base + '/empty', Path(tmp))

    def test_browser_rendering(self):
        self.assertIn(self.base + '/image.png', engine.rendered_images(self.base + '/dynamic'))

    def test_worker_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, 'engine.py'], input=json.dumps({'urls': [f'[page]({self.base})'], 'destination': tmp}),
                text=True, encoding='utf-8', capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(events[-1]['type'], 'done')
            self.assertTrue(any(e['type'] == 'file' for e in events))

    def test_gallery_markers(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            # gallery-dl's generic direct-image extractor, real subprocess.
            count, code, _ = engine.gallery_download(self.base + '/image.png', Path(tmp))
            self.assertEqual((count, code), (1, 0), output.getvalue())
            count, code, _ = engine.gallery_download(self.base + '/image.png', Path(tmp))
            self.assertEqual((count, code), (1, 0), output.getvalue())
            self.assertIn('"existing": true', output.getvalue())


if __name__ == '__main__':
    unittest.main()
