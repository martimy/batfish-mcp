# -*- coding: utf-8 -*-
"""
Copyright 2023-2026 Maen Artimy

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

"""
Batfish session singleton for the MCP server.

Replaces the former Streamlit ``st.session_state["bf_session"]`` coupling. The session
is created lazily from the ``BATFISH_SERVER`` environment variable (default
``127.0.0.1``) and shared by all tool calls, behind a lock so concurrent
MCP requests cannot race its creation.

Tests can inject a fake session (e.g. ``tests/fakes.FakeBatfish``) with
:func:`set_session`.
"""

import os
import threading
from pybatfish.client.session import Session

_lock = threading.Lock()
_session = None


def get_session() -> Session:
    """Returns the shared Batfish session, creating it on first use."""
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                host = os.getenv("BATFISH_SERVER", "127.0.0.1")
                _session = Session(host=host)
    return _session


def set_session(session) -> None:
    """Replaces the shared session (tests injecting a fake)."""
    global _session
    with _lock:
        _session = session


def clear_session() -> None:
    """Forgets the shared session; the next get_session() creates a new one."""
    global _session
    with _lock:
        _session = None
