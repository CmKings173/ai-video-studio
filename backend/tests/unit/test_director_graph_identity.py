import pytest

from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.workflow_registry import ApprovedWorkflow


@pytest.mark.parametrize(
    "class_type", ["MiniMaxH3Director", "ComfyMiniMaxH3Director", "StudioMiniMaxH3Director"]
)
def test_every_director_transport_is_classified_as_director(class_type):
    approved = ApprovedWorkflow("t2v", "1", {"7": {"class_type": class_type, "inputs": {}}}, {})
    assert GenerationService._is_director(approved)


def test_legacy_graph_is_not_director():
    approved = ApprovedWorkflow("t2v", "1", {"7": {"class_type": "Legacy", "inputs": {}}}, {})
    assert not GenerationService._is_director(approved)
