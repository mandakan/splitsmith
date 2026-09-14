"""Direct YouTube upload (issue #1000, phase 1).

``oauth`` holds the built-in OAuth client, the loopback consent flow and
the refresh-token store; ``client`` is the Data API client (resumable
upload, captions, thumbnail); ``upload`` reads the sidecar beside a
rendered MP4 and drives the client; ``cli`` is the ``splitsmith youtube``
verb group. Only ``cli`` imports typer.
"""
