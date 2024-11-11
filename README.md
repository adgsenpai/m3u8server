# Python HLS Reverse Proxy Server

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.6%2B-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.0%2B-green.svg)

## Overview

The **Python HLS Reverse Proxy Server** is a lightweight and efficient reverse proxy built with Flask. It intercepts HTTP requests for HLS (HTTP Live Streaming) playlists (`.m3u8`) and media segments (`.ts`), modifies the URIs to route them through the proxy, and serves the modified content to clients. This setup allows for enhanced control over streaming content, enabling features like logging, access control, and caching.

## Features

- **Master Playlist Modification**: Routes all stream variant URIs through the proxy.
- **Media Playlist Modification**: Routes all media segment URIs (`.ts` files) through the proxy.
- **Caching**: Implements caching for playlists to improve performance and reduce redundant requests.
- **Logging**: Comprehensive logging for monitoring and debugging.
- **Security**: Validates input URLs and can be extended with domain whitelisting.
- **Scalable Deployment**: Compatible with production-ready WSGI servers like Gunicorn.
