from flask import Flask, request, Response, abort, make_response
import requests
import m3u8
from urllib.parse import urlparse, urljoin, urlencode
from cachetools import TTLCache
import threading
from flask_cors import CORS

app = Flask(__name__)

# Enable CORS for all routes and origins
CORS(app, resources={r"/proxy": {"origins": "*"}})

# Configuration
PROXY_BASE_URL = "https://m3u8.adgstudios.co.za/proxy"

# Create a cache with a Time-To-Live (TTL) of 5 minutes for playlists and segments
playlist_cache = TTLCache(maxsize=1000, ttl=300)  # 5 minutes TTL
segment_cache = TTLCache(maxsize=10000, ttl=300)   # 5 minutes TTL

# Lock for thread-safe cache access
cache_lock = threading.Lock()

# Helper function to determine if the URL points to an M3U8 file
def is_m3u8(url):
    parsed = urlparse(url)
    return parsed.path.endswith('.m3u8')

# Helper function to extract filename from URL
def get_filename(url):
    parsed = urlparse(url)
    return parsed.path.split('/')[-1] or 'file'

# Helper function to extract the episode number (if needed for caching)
def get_episode_number(filename):
    # Assuming the filename contains 'ep.' followed by the episode number
    # Adjust the parsing logic according to the actual filename format
    if 'ep.' in filename:
        parts = filename.split('ep.')
        if len(parts) > 1:
            ep_part = parts[1]
            # Extract until the next dot
            ep_number = ep_part.split('.')[0]
            return ep_number
    return None

@app.route('/proxy')
def proxy():
    target_url = request.args.get('url')
    if not target_url:
        abort(400, description="Missing 'url' parameter.")

    # Extract the filename for Content-Disposition header
    filename = get_filename(target_url)

    if is_m3u8(target_url):
        # Check if the playlist is in cache
        with cache_lock:
            cached_playlist = playlist_cache.get(target_url)
        if cached_playlist:
            resp = make_response(cached_playlist)
            resp.headers['Content-Type'] = 'application/vnd.apple.mpegurl'
            resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
            # Allow CORS
            resp.headers['Access-Control-Allow-Origin'] = '*'
            return resp

        try:
            # Fetch the M3U8 playlist
            response = requests.get(target_url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            abort(502, description=f"Error fetching the URL: {e}")

        content_type = response.headers.get('Content-Type', '')

        # It's an M3U8 playlist; parse and modify it
        try:
            playlist = m3u8.loads(response.text)
        except Exception as e:
            abort(500, description=f"Error parsing M3U8 file: {e}")

        # Base URL for resolving relative segment URLs
        base_url = response.url  # Final URL after redirects

        # Modify each segment URI to go through the proxy
        for segment in playlist.segments:
            original_uri = segment.uri
            # Resolve relative URIs
            absolute_uri = urljoin(base_url, original_uri)
            # Create the proxied URL
            proxied_uri = f"{PROXY_BASE_URL}?{urlencode({'url': absolute_uri})}"
            segment.uri = proxied_uri

        # Similarly, handle playlists in EXT-X-STREAM-INF if present
        if playlist.is_variant:
            for playlist_variant in playlist.playlists:
                original_uri = playlist_variant.uri
                absolute_uri = urljoin(base_url, original_uri)
                proxied_uri = f"{PROXY_BASE_URL}?{urlencode({'url': absolute_uri})}"
                playlist_variant.uri = proxied_uri

        # Create a response with the modified playlist
        modified_playlist = playlist.dumps()

        # Cache the modified playlist
        with cache_lock:
            playlist_cache[target_url] = modified_playlist

        resp = make_response(modified_playlist)
        resp.headers['Content-Type'] = 'application/vnd.apple.mpegurl'
        resp.headers['Content-Length'] = len(modified_playlist)
        # Set Content-Disposition to suggest the original filename
        resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        # Allow CORS
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

    else:
        # Check if the segment is in cache
        with cache_lock:
            cached_segment = segment_cache.get(target_url)
        if cached_segment:
            resp = Response(cached_segment, mimetype='video/MP2T')
            resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
            # Allow CORS
            resp.headers['Access-Control-Allow-Origin'] = '*'
            return resp

        try:
            # Fetch the media segment
            response = requests.get(target_url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            abort(502, description=f"Error fetching the URL: {e}")

        content_type = response.headers.get('Content-Type', '')

        # Determine the appropriate content type
        if target_url.endswith('.ts'):
            mime_type = 'video/MP2T'
        else:
            # Fallback to the original content type
            mime_type = content_type or 'application/octet-stream'

        # Read the content and cache it
        content = response.content

        with cache_lock:
            segment_cache[target_url] = content

        resp = Response(content, mimetype=mime_type)
        # Set Content-Disposition to suggest the original filename
        resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        # Optionally, set Content-Length
        resp.headers['Content-Length'] = len(content)
        # Allow CORS
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

if __name__ == '__main__':
    # Run the Flask app
    # For production, consider using a production-ready server like Gunicorn
    app.run(host='0.0.0.0', port=8000, debug=True)
