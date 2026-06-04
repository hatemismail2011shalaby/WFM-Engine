# -----------------------------------------------------------------------------
# HITL Status & Heuristic History (append after the Shadow Mode panel)
# -----------------------------------------------------------------------------
def _render_hitl_panel():
    """Display automation suspension state and recent heuristics."""
    st.divider()
    st.subheader("Human‑in‑the‑Loop — Escalation Status")

    # Fetch HITL status from core.hitl_queue (fallback: API)
    hitl_status = None
    try:
        from core.hitl_queue import get_status as _hitl_get_status
        hitl_status = _hitl_get_status()
    except Exception:
        pass
    if not hitl_status:
        try:
            resp = requests.get(
                f"{API_BASE}/hitl/status",
                headers={"X-API-Key": API_KEY},
                timeout=3,
            )
            if resp.status_code == 200:
                hitl_status = resp.json()
        except Exception:
            pass

    if hitl_status:
        suspended = hitl_status.get("automation_suspended", False)
        failures = hitl_status.get("consecutive_failures", {})
        threshold = hitl_status.get("threshold", 3)

        col1, col2 = st.columns(2)
        with col1:
            if suspended:
                st.error("🚨 AUTOMATION SUSPENDED — Manual intervention required")
            else:
                st.success("✅ Automation active")
        with col2:
            if failures:
                st.metric("Max Consecutive Failures", max(failures.values()))
            else:
                st.caption("No failure counts")

        if suspended and st.button("Reset Suspension (Admin)"):
            try:
                requests.post(
                    f"{API_BASE}/hitl/reset",
                    headers={"X-API-Key": API_KEY},
                    timeout=3,
                )
                st.experimental_rerun()
            except Exception:
                pass
    else:
        st.info("HITL status not available")

    # Heuristic history from TMK store
    st.subheader("Recent Learned Heuristics (TMK Memory)")
    heuristics = []
    try:
        from core.tmk_store import fetch_recent_heuristics as _fetch_heuristics
        heuristics = _fetch_heuristics(20)
    except Exception:
        try:
            resp = requests.get(
                f"{API_BASE}/tmk/heuristics",
                headers={"X-API-Key": API_KEY},
                timeout=3,
            )
            if resp.status_code == 200:
                heuristics = resp.json()
        except Exception:
            pass

    if heuristics:
        st.dataframe(heuristics, use_container_width=True)
    else:
        st.caption("No heuristics recorded yet.")