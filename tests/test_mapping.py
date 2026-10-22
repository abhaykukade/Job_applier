from job_applier.forms.mapping import RESUME, Context, match_option, resolve_form
from job_applier.forms.models import FormField, Option

YN = [Option(label="Yes", value="1"), Option(label="No", value="0")]


def ctx(profile, bank=None, location="Remote - US"):
    return Context(profile, bank or {}, location)


def test_standard_fields(profile):
    fields = [
        FormField(key="first_name", label="First Name", required=True),
        FormField(key="last_name", label="Last Name", required=True),
        FormField(key="name", label="Full name", required=True),
        FormField(key="email", label="Email", type="email", required=True),
        FormField(key="resume", label="Resume/CV", type="file", required=True),
        FormField(key="urls[LinkedIn]", label="LinkedIn URL"),
        FormField(key="org", label="Current company"),
    ]
    res = resolve_form(fields, ctx(profile))
    assert res.answers == {
        "first_name": "Jordan", "last_name": "Lee", "name": "Jordan Lee", "email": "jordan@example.com",
        "resume": RESUME, "urls[LinkedIn]": "https://linkedin.com/in/jordanlee", "org": "Initrode",
    }
    assert res.unresolved == []


def test_work_authorization_uses_countries(profile):
    us = FormField(key="q1", label="Are you legally authorized to work in the United States?", type="select", options=YN, required=True)
    uk = FormField(key="q2", label="Are you authorized to work in the UK?", type="select", options=YN, required=True)
    generic = FormField(key="q3", label="Are you authorized to work for us in the country of this job?", type="select", options=YN, required=True)
    res = resolve_form([us, uk, generic], ctx(profile, location="Remote - US"))
    assert res.answers["q1"] == "Yes"
    assert res.answers["q2"] == "No"
    assert res.answers["q3"] == "Yes"  # falls back to the job's location
    res = resolve_form([generic], ctx(profile, location="Remote"))
    assert [f.key for f in res.unresolved] == ["q3"]  # unknown country -> ask, never guess


def test_sponsorship_and_eeo(profile):
    sponsor = FormField(key="s", label="Will you now or in the future require visa sponsorship?", type="select", options=YN, required=True)
    gender = FormField(key="gender", label="Gender", type="select", section="eeo",
                       options=[Option(label="Male", value="1"), Option(label="Decline To Self Identify", value="3")])
    vet = FormField(key="vet", label="Veteran Status", type="select", section="eeo",
                    options=[Option(label="I am not a protected veteran", value="2"), Option(label="I don't wish to answer", value="3")])
    res = resolve_form([sponsor, gender, vet], ctx(profile))
    assert res.answers == {"s": "No", "gender": "Decline To Self Identify", "vet": "I don't wish to answer"}


def test_unknown_required_is_held_and_optional_skipped(profile):
    go = FormField(key="q", label="How many years of professional Go experience do you have?", required=True)
    twitter = FormField(key="t", label="Twitter")
    res = resolve_form([go, twitter], ctx(profile))
    assert res.answers == {}
    assert [f.key for f in res.unresolved] == ["q"]


def test_answer_bank_is_used(profile):
    go = FormField(key="q", label="How many years of professional Go experience do you have?", required=True)
    bank = {"how many years of professional go experience do you have": "2"}
    assert resolve_form([go], ctx(profile, bank)).answers == {"q": "2"}


def test_generic_years_of_experience(profile):
    f = FormField(key="y", label="Years of professional experience", required=True)
    assert resolve_form([f], ctx(profile)).answers == {"y": "8"}


def test_match_option():
    opts = [Option(label="Yes, I am", value="y"), Option(label="No, I am not", value="n")]
    assert match_option("Yes", opts) == "Yes, I am"
    assert match_option("no", opts) == "No, I am not"
    assert match_option("maybe", opts) is None
    assert match_option("Decline to self-identify", [Option(label="Male", value="m")]) is None
