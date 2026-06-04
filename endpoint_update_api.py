# ---- HITL endpoints (secured) ----
@app.get("/hitl/status", dependencies=[Depends(verify_api_key)], tags=["hitl"])
async def hitl_status():
    from core.hitl_queue import get_status as _hs
    return _hs()

@app.post("/hitl/reset", dependencies=[Depends(verify_api_key)], tags=["hitl"])
async def hitl_reset():
    from core.hitl_queue import reset_suspension
    reset_suspension()
    return {"message": "automation resumed"}

# ---- TMK heuristics endpoint (secured) ----
@app.get("/tmk/heuristics", dependencies=[Depends(verify_api_key)], tags=["tmk"])
async def tmk_heuristics(limit: int = 20):
    from core.tmk_store import fetch_recent_heuristics as _fh
    return _fh(limit)