from wootify.bootstrap.app import app, create_app, default_container


def test_application_factory_owns_explicit_container() -> None:
    fresh = create_app()

    assert fresh is not app
    assert fresh.state.container is not default_container
    assert fresh.state.container.plugins.keys() == default_container.plugins.keys()


def test_legacy_and_canonical_entrypoints_share_default_app() -> None:
    from app.main import app as legacy_app
    from wootify.main import app as canonical_app

    assert legacy_app is app
    assert canonical_app is app


def test_factory_preserves_public_openapi_surface() -> None:
    assert create_app().openapi()["paths"] == app.openapi()["paths"]
