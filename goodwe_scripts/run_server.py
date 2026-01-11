#!/usr/bin/env python3
"""
Simple HTTP server to run the Solar Power Generation Dashboard
"""

import http.server
import socketserver
import os
from pathlib import Path

PORT = 8000

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        # Add CORS headers to allow local file access
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()

def main():
    # Change to the script's directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check if required files exist
    if not Path('index.html').exists():
        print("❌ Error: index.html not found in current directory")
        return
    
    if not Path('monthly_stats.json').exists():
        print("⚠️  Warning: monthly_stats.json not found")
        print("   The website will show an error until you add the JSON file")
        print()
    
    # Create server with SO_REUSEADDR to allow immediate restart
    socketserver.TCPServer.allow_reuse_address = True
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
        
        # Start server
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 Server stopped. Goodbye!")

if __name__ == "__main__":
    main()
