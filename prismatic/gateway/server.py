

@app.get("/api/pwp/kpi/sites")
async def pwp_kpi_list_sites() -> dict[str, Any]:
    try:
        from plugins.pwp.capabilities.publish_kpi_tracker import list_sites
        return {"sites": list_sites()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/pwp/kpi/sites/{slug}")
async def pwp_kpi_get_site(slug: str) -> dict[str, Any]:
    try:
        from plugins.pwp.capabilities.publish_kpi_tracker import load_site
        return load_site(slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Site KPI collection not found for slug: {slug}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/pwp/kpi/refresh")
async def pwp_kpi_refresh() -> dict[str, Any]:
    try:
        from plugins.pwp.capabilities.publish_kpi_tracker import list_sites
        return {"status": "ok", "sites_refreshed": len(list_sites())}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/pwp/kpi/publish-dashboard")
async def pwp_kpi_publish_dashboard(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
        publish_root = body.get("publish_root", "")
        if not publish_root:
            raise HTTPException(status_code=400, detail="publish_root required")
        from plugins.pwp.capabilities.publish_kpi_tracker import publish_publish_kpi_dashboard
        return publish_publish_kpi_dashboard(publish_root)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
