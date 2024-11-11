import os
from flask import Flask, request, Response
import requests
import m3u8
from urllib.parse import urlparse, urljoin, urlencode
import logging
from flask_caching import Cache

app = Flask(__name__)

# Configuration
PROXY_BASE_URL = "http://localhost:9000"  # Update if running on a different host or port
TIMEOUT = 10  # Seconds for HTTP requests

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Setup Caching
cache_config = {
    "CACHE_TYPE": "SimpleCache",       # Use SimpleCache for development; consider RedisCache for production
    "CACHE_DEFAULT_TIMEOUT": 300       # Cache timeout in seconds (e.g., 5 minutes)
}
app.config.from_mapping(cache_config)
cache = Cache(app)

def is_absolute_url(url):
    return bool(urlparse(url).netloc)

def construct_proxy_url(target_url):
    """
    Constructs the proxy URL for a given target URL.
    """
    query = urlencode({'url': target_url})
    return f"{PROXY_BASE_URL}/proxy?{query}"

def is_master_playlist(playlist):
    """
    Determines if the playlist is a master playlist.
    """
    return playlist.is_variant

@app.route('/proxy')
@cache.cached(query_string=True)  # Cache based on the full query string
def proxy():
    """
    The main proxy endpoint that handles incoming requests.
    Expects a 'url' query parameter specifying the target URL.
    """
    target_url = request.args.get('url')
    if not target_url:
        logger.error("Missing 'url' query parameter.")
        return Response("Missing 'url' query parameter.", status=400)

    # Validate the target URL
    parsed_target = urlparse(target_url)
    if not parsed_target.scheme or not parsed_target.netloc:
        logger.error(f"Invalid 'url' parameter: {target_url}")
        return Response("Invalid 'url' parameter.", status=400)

    # Determine the type of content based on the URL extension
    path = parsed_target.path.lower()

    try:
        if path.endswith('.m3u8'):
            return handle_m3u8(target_url)
        else:
            return handle_other(target_url)
    except requests.RequestException as e:
        logger.exception(f"Error fetching target URL: {e}")
        return Response(f"Error fetching target URL: {e}", status=502)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return Response(f"Unexpected error: {e}", status=500)

@cache.memoize(timeout=300)  # Cache the result of handle_m3u8 for 5 minutes
def handle_m3u8(target_url):
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
            proxied_uri = construct_proxy_url(original_uri)
            logger.info(f"Modifying stream URI: {original_uri} -> {proxied_uri}")
            playlist_item.uri = proxied_uri
    else:
        logger.info("Identified as Media Playlist.")
        # Media playlist: modify segment URLs
        for segment in playlist.segments:
            original_uri = segment.uri
            # Handle relative URLs
            if not is_absolute_url(original_uri):
                original_uri = urljoin(target_url, original_uri)
            proxied_uri = construct_proxy_url(original_uri)
            logger.info(f"Modifying segment URI: {original_uri} -> {proxied_uri}")
            segment.uri = proxied_uri

    modified_playlist = playlist.dumps()

    # Save the modified playlist to a file with the original name derived from the URL
    filename = os.path.basename(parsed_target.path)
    with open(filename, 'w') as file:
        file.write(modified_playlist)
    logger.info(f"Saved modified playlist as {filename}")

    return Response(modified_playlist, mimetype='application/vnd.apple.mpegurl')

@cache.memoize(timeout=300)  # Cache the result of handle_other for 5 minutes
def handle_other(target_url):
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

    # Stream the response content to the client
    def generate():
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    # Determine the MIME type based on the content
    content_type = response.headers.get('Content-Type', 'application/octet-stream')
    logger.info(f"Serving content with MIME type: {content_type}")
    return Response(generate(), content_type=content_type)

@app.route('/')
def index():
    """
    Simple index page with instructions.
    """
    return """
    <h1>Python Reverse Proxy Server</h1>
    <p>Use the <code>/proxy?url=TARGET_URL</code> endpoint to access content via the proxy.</p>
    <p>Example: <a href="/proxy?url=https://www.example.com/playlist.m3u8">Proxy Playlist</a></p>
    """

if __name__ == '__main__':
    # Optionally, allow the host and port to be set via environment variables
    host = os.environ.get('PROXY_HOST', '0.0.0.0')
    port = int(os.environ.get('PROXY_PORT', 9000))
    app.run(host=host, port=port, threaded=True)