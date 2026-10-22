from job_applier.ats import detect_company, get_ats, parse_board_url


def test_greenhouse_listing(client):
    jobs = get_ats("greenhouse", client).list_jobs("acme", "Acme")
    assert [j.external_id for j in jobs] == ["4001001", "4001002", "4001003"]
    job = jobs[0]
    assert job.workplace == "remote"
    assert job.apply_url == "https://job-boards.greenhouse.io/acme/jobs/4001001"
    assert "5+ years Python" in job.description and "&lt;" not in job.description
    assert job.posted_at is not None and job.posted_at.year == 2026


def test_greenhouse_form(client):
    fields = get_ats("greenhouse", client).get_form("acme", "4001001", "")
    by_key = {f.key: f for f in fields}
    assert by_key["resume"].type == "file" and by_key["resume"].required
    assert "resume_text" not in by_key
    assert by_key["email"].type == "email"
    assert by_key["question_101"].type == "select"
    assert by_key["question_101"].option_labels == ["Yes", "No"]
    assert by_key["gender"].section == "eeo"
    assert by_key["first_name"].meta["selector"] == '[id="first_name"]'


def test_lever_listing_and_form(client):
    lever = get_ats("lever", client)
    jobs = lever.list_jobs("globex", "Globex")
    assert jobs[0].workplace == "hybrid"
    assert "AWS" in jobs[0].description
    fields = {f.key: f for f in lever.get_form("globex", jobs[0].external_id, jobs[0].apply_url)}
    assert fields["resume"].type == "file" and fields["resume"].required
    assert fields["name"].label == "Full name" and fields["name"].required
    assert fields["cards[abc][field0]"].type == "radio"
    assert fields["cards[abc][field0]"].label == "Are you authorized to work in the US?"
    assert fields["cards[abc][field0]"].required
    assert fields["cards[abc][field1]"].option_labels == ["Kafka", "Spark"]
    assert fields["cards[abc][field2]"].option_labels == ["Seattle", "San Francisco"]
    assert fields["eeo[gender]"].section == "eeo"
    assert "accountId" not in fields


def test_ashby_listing_and_form(client):
    ashby = get_ats("ashby", client)
    jobs = ashby.list_jobs("initech", "Initech")
    assert len(jobs) == 1 and jobs[0].workplace == "remote"
    fields = {f.key: f for f in ashby.get_form("initech", jobs[0].external_id, jobs[0].apply_url)}
    assert fields["_systemfield_resume"].type == "file"
    assert fields["q_sponsor"].type == "boolean" and fields["q_sponsor"].option_labels == ["Yes", "No"]
    assert fields["q_level"].option_labels == ["Core", "Growth"]


def test_parse_board_url():
    assert parse_board_url("https://boards.greenhouse.io/acme/jobs/1") == ("greenhouse", "acme")
    assert parse_board_url("https://job-boards.greenhouse.io/acme") == ("greenhouse", "acme")
    assert parse_board_url("https://boards.greenhouse.io/embed/job_board?for=acme") == ("greenhouse", "acme")
    assert parse_board_url("jobs.lever.co/globex") == ("lever", "globex")
    assert parse_board_url("https://jobs.ashbyhq.com/initech/abc") == ("ashby", "initech")
    assert parse_board_url("https://example.com/careers") is None


def test_detect_company_by_name(client):
    assert detect_company("Acme Inc.", client) == [("greenhouse", "acme")]
    assert detect_company("Globex", client) == [("lever", "globex")]
    assert detect_company("Nobody Corp", client) == []
