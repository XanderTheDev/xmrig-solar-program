#!/usr/bin/env python3
"""
Simple local HTTP server for previewing the Solar Power Generation Dashboard.
This script serves static files and adds small enhancements like:
 - CORS headers (for local JavaScript fetches)
 - Cache disabling (so data updates are reflected immediately)
 - Auto-checks for required project files
"""

import http.server
import socketserver
import os
from pathlib import Path

# Port where the development server will be available.
PORT = 8000


class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler that injects HTTP headers useful for local dev."""

    def end_headers(self):
        # Allow any origin to access local resources.
        # This prevents CORS errors when JavaScript fetches JSON files locally.
        self.send_header('Access-Control-Allow-Origin', '*')

        # Disable caching to ensure file changes show up immediately in the browser.
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')

        # Call parent class to finish header sending.
        super().end_headers()


def main():
    """Entry point: changes to project directory, validates files, then starts server."""

    # Make sure the server serves files relative to the script's location,
    # regardless of where you run it from.
    script_dir = Path(__file__).parent
    os.chdir(script_dir)

    # Basic sanity checks for essential dashboard files.
    if not Path('index.html').exists():
        print("❌ Error: index.html not found in current directory")
        return

    if not Path('monthly_stats.json').exists():
        print("⚠️  Warning: monthly_stats.json not found")
        print("   The dashboard will show an error until this file is provided.")
        print()

    # Allow immediate restart without waiting for the socket to fully release.
    socketserver.TCPServer.allow_reuse_address = True

    # Create and start the HTTP server.
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print("=" * 60)
        print("🌞 Solar Power Generation Dashboard")
        print("=" * 60)
        print(f"✅ Server running at: {url}")
        print(f"📁 Serving files from: {script_dir}")
        print()
        print("🌐 Opening browser...")
        print("⏹️  Press Ctrl+C to stop the server")
        print("=" * 60)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 Server stopped. Goodbye!")


if __name__ == "__main__":
    main()
