import pytest
from pydantic import ValidationError

from apps.api.app.schemas.api import ResourcePatch, ScenePatch, VideoPatch


@pytest.mark.parametrize("schema", [ResourcePatch, VideoPatch, ScenePatch])
def test_mutation_patch_rejects_explicit_null_for_non_nullable_fields(schema):
    values = {
        ResourcePatch: {"name": None},
        VideoPatch: {"brief": None},
        ScenePatch: {"prompt": None},
    }[schema]
    with pytest.raises(ValidationError):
        schema(**values)
