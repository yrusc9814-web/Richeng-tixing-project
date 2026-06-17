from http.server import BaseHTTPRequestHandler, HTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)

        try:
            data = json.loads(body.decode('utf-8'))
        except Exception:
            data = {}

        response = {
            "success": True,
            "message_id": f"wx_{data.get('task_id', 'unknown')}",
            "received": True,
        }

        resp_bytes = json.dumps(response).encode('utf-8')

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)


def run():
    server = HTTPServer(('127.0.0.1', 8899), Handler)
    print('Webhook server running at http://127.0.0.1:8899')
    server.serve_forever()


if __name__ == '__main__':
    run()
