from datetime import timedelta

from job_applier.db import utcnow
from job_applier.llm import strict_schema
from job_applier.pipeline import ai
from job_applier.pipeline.filters import check
from job_applier.resume.tailoring import apply_edits, new_terms, to_proposed
from job_applier.settings import SearchSettings


def test_filters():
    cfg = SearchSettings(titles_include=["Backend Engineer"], locations=["United States", "US"], workplace=["remote", "hybrid"])
    now = utcnow()
    assert check("Senior Backend Engineer", "", "Remote - US", "remote", now, cfg) is None
    assert check("Backend Engineering Intern", "", "Remote", "remote", now, cfg) is not None
    assert check("Frontend Engineer", "", "Remote", "remote", now, cfg) == "title not in your target titles"
    assert check("Backend Engineer", "", "London, UK", "onsite", now, cfg) == "onsite roles excluded"
    assert check("Backend Engineer", "", "Remote - EMEA", "remote", now, cfg).startswith("location")
    assert check("Backend Engineer", "", "Remote", "remote", now, cfg) is None  # unrestricted remote
    assert "days" in check("Backend Engineer", "", "Remote", "remote", now - timedelta(days=60), cfg)
    # "intern" must match as a word, not inside "internal"
    assert check("Backend Engineer, Internal Tools", "", "Remote", "remote", now, cfg) is None


def test_strict_schema_requires_everything():
    schema = strict_schema(ai.TailorResult)
    edit = schema["properties"]["edits"]["items"]
    assert edit["additionalProperties"] is False
    assert set(edit["required"]) == set(edit["properties"])
    assert "$defs" not in schema and "$ref" not in str(schema)


def edit(**kw):
    base = dict(experience_index=None, bullet_index=None, skill_group_index=None, new_text=None, new_order=None, rationale="r", claim=None)
    return ai.TailorEdit(**{**base, **kw})


def test_proposals_validate_and_flag_claims(resume):
    assert to_proposed(edit(kind="bullet", experience_index=9, bullet_index=0, new_text="x"), resume) is None
    assert to_proposed(edit(kind="bullets_reorder", experience_index=0, new_order=[0, 1]), resume) is None  # no-op
    rewrite = to_proposed(edit(kind="bullet", experience_index=0, bullet_index=0, new_text="Built payment APIs in Python on Kubernetes serving 2M requests/day"), resume)
    assert rewrite.claims == ["Kubernetes"]  # caught even though the model didn't flag it
    skill = to_proposed(edit(kind="skill_add", skill_group_index=0, new_text="Python"), resume)
    assert skill is None  # already listed


def test_new_terms(resume):
    assert new_terms("Built payment APIs in Python", resume.plain_text()) == []
    assert new_terms("Shipped Terraform and Go services", resume.plain_text()) == ["Terraform", "Go"]


def test_apply_edits(resume):
    out = apply_edits(resume, [
        ("bullets_reorder", {"exp": 0, "order": [1, 0]}, ""),
        ("bullet", {"exp": 0, "bullet": 0}, "Built Python payment APIs (2M req/day)"),
        ("skill_add", {"group": 0, "skill": "Kubernetes"}, ""),
        ("summary", {}, "New summary"),
    ])
    assert out.experience[0].bullets == ["Ran the on-call rotation", "Built Python payment APIs (2M req/day)"]
    assert out.skills[0].items[-1] == "Kubernetes"
    assert out.summary == "New summary"
    assert resume.summary.startswith("Backend engineer")  # master untouched
