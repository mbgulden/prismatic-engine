import sys
from pathlib import Path

pwp_dir = Path(__file__).resolve().parent
shipped_plugins_dir = pwp_dir.parent
engine_dir = shipped_plugins_dir.parent

for p in [str(engine_dir), str(shipped_plugins_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import prismatic.shipped_plugins as shipped_plugins
    import prismatic.shipped_plugins.pwp as pwp_mod
    import prismatic.shipped_plugins.pwp.capabilities as caps_mod
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker as kpi_tracker
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as kpi_tracker_inner
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form as funnel_form
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status as linear_status
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator as cron_orchestrator
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.runtime_values as runtime_values
    import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.pwp_kpi_site_registry as pwp_kpi_site_registry
    import prismatic.shipped_plugins.pwp.capabilities.provision_site as provision_site
    import prismatic.shipped_plugins.pwp.capabilities.provision_site.auth_loader as auth_loader

    sys.modules["plugins"] = shipped_plugins
    sys.modules["plugins.pwp"] = pwp_mod
    sys.modules["plugins.pwp.capabilities"] = caps_mod
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker"] = kpi_tracker
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker"] = kpi_tracker_inner
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.funnel_form"] = funnel_form
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.linear_status"] = linear_status
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.cron_orchestrator"] = cron_orchestrator
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.runtime_values"] = runtime_values
    sys.modules["plugins.pwp.capabilities.publish_kpi_tracker.pwp_kpi_site_registry"] = pwp_kpi_site_registry
    sys.modules["plugins.pwp.capabilities.provision_site"] = provision_site
    sys.modules["plugins.pwp.capabilities.provision_site.auth_loader"] = auth_loader
except Exception:
    pass
