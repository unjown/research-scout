def test_dashboard_does_not_start_inference_on_import() -> None:
    import local.dashboard as dashboard
    assert dashboard.BUSY is False
    assert callable(dashboard.approve)


def test_worker_entrypoint_and_manual_workflow_remain() -> None:
    from local.worker import run_approved
    workflow = open(".github/workflows/daily-discovery.yml", encoding="utf-8").read()
    assert callable(run_approved)
    assert "workflow_dispatch:" in workflow
