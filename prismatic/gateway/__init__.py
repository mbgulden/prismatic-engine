"""
Prismatic Gateway — event bus, IPC bridge, and WebSocket broadcaster.

Provides the event infrastructure for the Prismatic Engine:
- EventBus: async pub/sub for swarm events
- IPC Bridge: Unix socket + HTTP ingestion for external processes
- WS Broadcaster: WebSocket server for real-time dashboard streaming
"""
