import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class WeChatWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/wechat/webhook':
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(length) if length else b'{}'
        try:
            data = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            data = {}
        body = {
            'success': True,
            'message_id': f"wx_{data.get('task_id', 'unknown')}",
            'received': True,
        }
        encoded = json.dumps(body).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        print(format % args)


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8899), WeChatWebhookHandler).serve_forever()
