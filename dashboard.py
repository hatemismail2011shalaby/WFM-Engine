"""
dashboard.py — LinePilot RTA Command Center
Streamlit UI with live metacognitive reflector polling for Shadow Mode Savings.
"""

import os
import time
import threading
from typing import Any, Dict, Optional

import streamlit as st
import requests

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
API_BASE = os.environ.get("LINEPILOT_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("LINEPILOT_API_KEY", "")
POLL_INTERVAL = int(os.environ.get("LINEPILOT_POLL_INTERVAL", "5"))

# Defensive imports: direct module access as fallback if API is localhost
try:
    from core import reflector as _reflector_module
except Exception:
    _reflector_module = None

try:
    from core import tmk_store as _tmk_store_module
except Exception:
    _tmk_store_module = None

# -----------------------------------------------------------------------------
# Reflector Snapshot Fetcher (API-first, defensive fallbacks)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=POLL_INTERVAL)
def _fetch_reflector_snapshot() -> Optional[Dict[str, Any]]:
    """
    Fetch latest reflector output.
    Strategy 1: HTTP GET /reflector/latest (production path)
    Strategy 2: Direct module introspection (local dev fallback)
    Strategy 3: TMK store query (last resort)
    """
    # Strategy 1: API endpoint
    try:
        resp = requests.get(
            f"{API_BASE}/reflector/latest",
            headers={"X-API-Key": API_KEY},
            timeout=3,
        )
        if resp.status_code == 200:
            payload = resp.json()
            if payload and payload.get("source") == "reflector":
                return payload
    except Exception:
        pass  # Fail silently; try fallbacks

    # Strategy 2: direct module call
    if _reflector_module:
        for fn_name in ("get_latest_snapshot", "latest_snapshot", "get_snapshot", "latest"):
            try:
                fn = getattr(_reflector_module, fn_name)
                if callable(fn):
                    out = fn()
                    return _to_dict(out)
            except Exception:
                continue
        for attr_name in ("LATEST_SNAPSHOT", "latest_snapshot", "LAST_OUTPUT", "last_output"):
            try:
                out = getattr(_reflector_module, attr_name)
                if out is not None:
                    return _to_dict(out)
            except Exception:
                continue

    # Strategy 3: tmk_store fallback
    if _tmk_store_module:
        for fn_name in ("fetch_latest_reflector_snapshot", "get_latest_reflector_snapshot", "fetch_latest"):
            try:
                fn = getattr(_tmk_store_module, fn_name)
                if callable(fn):
                    out = fn()
                    return _to_dict(out)
            except Exception:
                continue

    return None


def _to_dict(obj: Any) -> Optional[Dict[str, Any]]:
    """Safely coerce a Pydantic model or object to a dict."""
    if isinstance(obj, dict):
        return obj
    try:
        return obj.model_dump()  # Pydantic v2
    except Exception:
        pass
    try:
        return obj.dict()  # Pydantic v1
    except Exception:
        pass
    try:
        return dict(obj.__dict__)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Shadow Mode Savings UI Components
# -----------------------------------------------------------------------------
def _render_shadow_panel(snapshot: Optional[Dict[str, Any]]) -> None:
    """Render the Shadow Mode Savings panel from a reflector snapshot."""
    st.subheader("Metacognitive Reflector — Shadow Mode")

    if not snapshot:
        st.warning("No live reflector snapshot. Check API connectivity or engine state.")
        return

    cols = st.columns(4)
    with cols[0]:
        delta = snapshot.get("capacity_delta")
        st.metric("Capacity Delta", f"{delta:+.1f}" if delta is not None else "—", "FTE")
    with cols[1]:
        savings = snapshot.get("estimated_savings")
        st.metric("Est. Savings", f"${savings:,.0f}" if savings is not None else "—", "per interval")
    with cols[2]:
        dev_class = snapshot.get("deviation_class", "none")
        st.metric("Deviation", str(dev_class).upper())
    with cols[3]:
        heuristic = snapshot.get("heuristic_applied", False)
        st.metric("Heuristic", "APPLIED" if heuristic else "IDLE")

    ts = snapshot.get("timestamp")
    if ts:
        st.caption(f"Last updated: {ts}")

    with st.expander("Raw reflector snapshot (debug)"):
        st.json(snapshot)


# -----------------------------------------------------------------------------
# Background Poller (daemon thread writes to session_state)
# -----------------------------------------------------------------------------
def _start_poller():
    """Start a background thread that polls the reflector and writes to st.session_state."""
    if "reflector_poller_running" in st.session_state:
        return  # already started

    st.session_state.reflector_poller_running = True

    def _loop():
        while True:
            try:
                st.session_state.latest_snapshot = _fetch_reflector_snapshot()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="reflector-poller")
    t.start()


# -----------------------------------------------------------------------------
# Main Dashboard Layout
# -----------------------------------------------------------------------------
st.set_page_config(page_title="LinePilot Command Center", layout="wide")
st.title("LinePilot — Real-Time Workforce Command Center")

# Start background polling thread
_start_poller()

# Shadow Mode Savings panel (live)
snapshot = st.session_state.get("latest_snapshot")
_render_shadow_panel(snapshot)

# Auto-refresh UI every POLL_INTERVAL seconds
st_autorefresh = st.empty()
st_autorefresh.markdown(
    f'<meta http-equiv="refresh" content="{POLL_INTERVAL}">',
    unsafe_allow_html=True,
)

st.divider()
st.caption(f"API endpoint: {API_BASE}  ·  Poll interval: {POLL_INTERVAL}s  ·  v0.2.0-pilot")