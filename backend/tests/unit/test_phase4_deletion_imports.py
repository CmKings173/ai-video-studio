from importlib import import_module


def test_application_import_registers_all_deletion_routes() -> None:
    main = import_module("apps.api.app.main")

    deletion_router = main.deletions.router
    assert deletion_router in main.routers
    route_paths = {route.path for route in deletion_router.routes}
    assert "/videos/{video_id}/deletion-preview" in route_paths
    assert "/scenes/{scene_id}/deletion-preview" in route_paths
    assert "/generations/{generation_id}/deletion-preview" in route_paths
    assert "/final-versions/{final_version_id}/deletion-preview" in route_paths
