import os
from flask import Flask, request, Response, abort, jsonify
import requests
import m3u8
from urllib.parse import urlparse, urljoin
import logging
from flask_caching import Cache
from datetime import datetime
from functools import wraps
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# =======================
# Configuration
# =======================

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Base URL for the proxy server (should match your server's domain)
PROXY_BASE_URL = os.getenv("PROXY_BASE_URL", "https://m3u8.adgstudios.co.za")

# Timeout for HTTP requests in seconds
TIMEOUT = int(os.getenv("TIMEOUT", "10"))

# Directory to save modified playlists
SAVE_DIRECTORY = os.getenv("SAVE_DIRECTORY", "modified_playlists")

# Allowed domains for proxying
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "www088.anzeat.pro,www083.anzeat.pro").split(',')

# =======================
# Setup Logging
# =======================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("proxy_server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =======================
# Setup Caching
# =======================

cache_config = {
    "CACHE_TYPE": "SimpleCache",       # Use SimpleCache for development; consider RedisCache for production
    "CACHE_DEFAULT_TIMEOUT": 300       # Cache timeout in seconds (e.g., 5 minutes)
}
app = Flask(__name__)
app.config.from_mapping(cache_config)
cache = Cache(app)

# =======================
# Setup Rate Limiting
# =======================

limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# =======================
# Route History Tracking
# =======================

# Simple in-memory history tracking (can be replaced with persistent storage)
route_history = []

def log_route(original_url, proxied_url):
    """
    Logs the mapping between the original URL and the proxied URL.
    """
    timestamp = datetime.utcnow().isoformat()
    entry = {
        "timestamp": timestamp,
        "original_url": original_url,
        "proxied_url": proxied_url
    }
    route_history.append(entry)
    logger.info(f"Route logged: {original_url} -> {proxied_url}")

# =======================
# Helper Functions
# =======================

def is_absolute_url(url):
    """Check if a URL is absolute."""
    return bool(urlparse(url).netloc)

def construct_proxy_url(requested_path):
    """
    Constructs the proxy URL for a given requested path.
    Example:
        requested_path = 'www088.anzeat.pro/streamhls/.../ep.1.1703914189.m3u8'
        proxy_url = 'https://m3u8.adgstudios.co.za/proxy/www088.anzeat.pro/streamhls/.../ep.1.1703914189.m3u8'
    """
    return f"{PROXY_BASE_URL}/proxy/{requested_path}"

def get_target_url(requested_path):
    """
    Constructs the target URL based on the requested path.
    Example:
        requested_path = 'www088.anzeat.pro/streamhls/.../ep.1.1703914189.m3u8'
        target_url = 'https://www088.anzeat.pro/streamhls/.../ep.1.1703914189.m3u8'
    """
    return f"https://{requested_path}"

def is_master_playlist(playlist):
    """
    Determines if the playlist is a master playlist.
    A master playlist contains variant streams.
    """
    return playlist.is_variant

def require_api_key(f):
    """
    Decorator to require an API key for accessing certain endpoints.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-KEY')
        if api_key != os.getenv("API_KEY", "your_default_api_key"):
            logger.warning("Unauthorized access attempt.")
            abort(403, description="Forbidden: Invalid or missing API key.")
        return f(*args, **kwargs)
    return decorated

# =======================
# Proxy Endpoint
# =======================

@app.route('/proxy/<path:requested_path>')
@limiter.limit("100 per hour")  # Apply rate limiting to this endpoint
@cache.cached()  # Cache the response based on the requested path
def proxy(requested_path):
    """
    The main proxy endpoint that handles incoming requests.
    Maps /proxy/<requested_path> to the target URL.
    """
    target_url = get_target_url(requested_path)
    
    # Validate the target URL
    parsed_target = urlparse(target_url)
    if not parsed_target.scheme or not parsed_target.netloc:
        logger.error(f"Invalid target URL: {target_url}")
        return Response("Invalid target URL.", status=400)
    
    # Security: Ensure the target URL is in the allowed domains
    if parsed_target.netloc not in ALLOWED_DOMAINS:
        logger.error(f"Disallowed target URL: {target_url}")
        return Response("Disallowed target URL.", status=403)
    
    # Determine the type of content based on the URL extension
    path = parsed_target.path.lower()
    
    try:
        if path.endswith('.m3u8'):
            return handle_m3u8(target_url, requested_path)
        else:
            return handle_other(target_url, requested_path)
    except requests.RequestException as e:
        logger.exception(f"Error fetching target URL: {e}")
        return Response(f"Error fetching target URL: {e}", status=502)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return Response(f"Unexpected error: {e}", status=500)

# =======================
# Handle M3U8 Playlists
# =======================

@cache.memoize(timeout=300)  # Cache the result of handle_m3u8 for 5 minutes
def handle_m3u8(target_url, requested_path):
    """
    Fetches the M3U8 playlist, modifies it to route stream and segment URLs through the proxy, and serves it.
    Handles both master and media playlists.
    """
    logger.info(f"Fetching M3U8 playlist: {target_url}")
    response = requests.get(target_url, timeout=TIMEOUT)
    response.raise_for_status()
    
    playlist = m3u8.M3U8(response.text, base_uri=target_url)
    
    if is_master_playlist(playlist):
        logger.info("Identified as Master Playlist.")
        # Master playlist: modify stream URLs
        for playlist_item in playlist.playlists:
            original_uri = playlist_item.uri
            # Handle relative URLs
            if not is_absolute_url(original_uri):
                original_uri = urljoin(target_url, original_uri)
            # Extract the path relative to the domain
            parsed_original = urlparse(original_uri)
            relative_path = parsed_original.netloc + parsed_original.path
            proxied_uri = construct_proxy_url(relative_path.lstrip('/'))
            logger.debug(f"Modifying stream URI: {original_uri} -> {proxied_uri}")
            playlist_item.uri = proxied_uri
            # Log the route
            log_route(original_url=original_uri, proxied_url=proxied_uri)
    else:
        logger.info("Identified as Media Playlist.")
        # Media playlist: modify segment URLs
        for segment in playlist.segments:
            original_uri = segment.uri
            # Handle relative URLs
            if not is_absolute_url(original_uri):
                original_uri = urljoin(target_url, original_uri)
            # Extract the path relative to the domain
            parsed_original = urlparse(original_uri)
            relative_path = parsed_original.netloc + parsed_original.path
            proxied_uri = construct_proxy_url(relative_path.lstrip('/'))
            logger.debug(f"Modifying segment URI: {original_uri} -> {proxied_uri}")
            segment.uri = proxied_uri
            # Log the route
            log_route(original_url=original_uri, proxied_url=proxied_uri)
    
    modified_playlist = playlist.dumps()
    
    # Use the original filename for the modified playlist
    filename = os.path.basename(requested_path)
    
    # Ensure the save directory exists
    os.makedirs(SAVE_DIRECTORY, exist_ok=True)
    
    # Full path to save the file
    file_path = os.path.join(SAVE_DIRECTORY, filename)
    
    # Save the modified playlist to the file
    try:
        with open(file_path, 'w') as file:
            file.write(modified_playlist)
        logger.info(f"Saved modified playlist as {file_path}")
    except Exception as e:
        logger.exception(f"Failed to save modified playlist: {e}")
        return Response(f"Failed to save modified playlist: {e}", status=500)
    
    # Set the Content-Disposition header to prompt file download with the original filename
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    
    return Response(modified_playlist, mimetype='application/vnd.apple.mpegurl', headers=headers)

# =======================
# Handle Non-M3U8 Content
# =======================

@cache.memoize(timeout=300)  # Cache the result of handle_other for 5 minutes
def handle_other(target_url, requested_path):
    """
    Fetches and serves non-M3U8 content (e.g., stream segments).
    """
    logger.info(f"Fetching non-M3U8 content: {target_url}")
    headers = {}
    # Forward the original request headers except Host to the target
    for header in request.headers:
        if header[0].lower() != 'host':
            headers[header[0]] = header[1]
    
    response = requests.get(target_url, headers=headers, stream=True, timeout=TIMEOUT)
    response.raise_for_status()
    
    # Log the route
    proxied_url = construct_proxy_url(requested_path)
    log_route(original_url=target_url, proxied_url=proxied_url)
    
    # Stream the response content to the client
    def generate():
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    
    # Determine the MIME type based on the content
    content_type = response.headers.get('Content-Type', 'application/octet-stream')
    logger.info(f"Serving content with MIME type: {content_type}")
    return Response(generate(), content_type=content_type)

# =======================
# Route History Endpoint
# =======================

@app.route('/history')
@require_api_key
def history():
    """
    Returns the route history in JSON format.
    """
    return jsonify(route_history)

# =======================
# Index Route
# =======================

@app.route('/')
def index():
    """
    Simple index page with instructions.
    """
    return """
    <h1>Python Reverse Proxy Server</h1>
    <p>Use the <code>/proxy/<em>target_domain/path/to/file</em></code> endpoint to access content via the proxy.</p>
    <p>Examples:</p>
    <ul>
        <li><a href="/proxy/www088.anzeat.pro/streamhls/0b594d900f47daabc194844092384914/ep.1.1703914189.m3u8">Proxy Playlist 1</a></li>
        <li><a href="/proxy/www083.anzeat.pro/streamhls/6d59b534bed63f3ac8097a6033657c95/ep.1.1703911043.m3u8">Proxy Playlist 2</a></li>
    </ul>
    <p>To view route history, access <code>/history</code> endpoint with proper API key.</p>
    """

# =======================
# Error Handlers
# =======================

@app.errorhandler(403)
def forbidden(e):
    return jsonify(error="Forbidden: You don't have permission to access this resource."), 403

@app.errorhandler(404)
def not_found(e):
    return jsonify(error="Not Found: The requested resource could not be found."), 404

@app.errorhandler(429)
def too_many_requests(e):
    return jsonify(error="Too Many Requests: You have exceeded your rate limit."), 429

@app.errorhandler(500)
def internal_error(e):
    return jsonify(error="Internal Server Error: An unexpected error occurred."), 500

# =======================
# Run the Flask App
# =======================

if __name__ == '__main__':
    # Allow the host and port to be set via environment variables
    host = os.getenv('PROXY_HOST', '0.0.0.0')
    port = int(os.getenv('PROXY_PORT', '9000'))
    app.run(host=host, port=port, threaded=True)
