"""Mock auth gateway server for integration testing."""

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

logger = logging.getLogger(__name__)


class MockAuthGatewayHandler(BaseHTTPRequestHandler):
    """Handler for mock auth gateway requests."""

    def log_message(self, format, *args):
        """Override to use logger instead of stderr."""
        logger.info(f"mock-auth-gateway: {format % args}")

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/auth":
            self._handle_auth()
        elif self.path == "/health":
            self._handle_health()
        else:
            self.send_error(404, "Not Found")

    def _handle_health(self):
        """Handle health check."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "healthy"}).encode())

    def _handle_auth(self):
        """Handle auth validation.

        Expects: Authorization: Bearer <token>
        Returns: {"user_id": "<user_id>"}

        Token format can be:
        - "user-id:token-value" -> extracts user-id
        - "sk-voxhelm-xyz" -> returns "user-sk-voxhelm"
        - Any other format -> uses first part before : or whole token
        """
        auth_header = self.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            self.send_error(401, "Missing or invalid Authorization header")
            return

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Extract user_id from token
        # Format: "user-id:token-value" or just "token"
        if ":" in token:
            user_id = token.split(":")[0]
        else:
            # For tokens without :, use first 8-16 chars as identifier
            user_id = f"user-{token[:16]}"

        response = {"user_id": user_id}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())
        logger.info(f"Authenticated user_id={user_id}")


class MockAuthGateway:
    """Mock auth gateway server for testing."""

    def __init__(self, host="localhost", port=8001):
        """Initialize mock auth gateway.

        Args:
            host: Host to bind to
            port: Port to bind to
        """
        self.host = host
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        """Start the mock auth gateway server."""
        self.server = HTTPServer((self.host, self.port), MockAuthGatewayHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Mock auth gateway started on {self.host}:{self.port}")

    def stop(self):
        """Stop the mock auth gateway server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            logger.info("Mock auth gateway stopped")

    @property
    def url(self):
        """Get the base URL of the mock server."""
        return f"http://{self.host}:{self.port}"
