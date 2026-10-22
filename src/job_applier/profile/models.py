"""The master resume and the personal profile used to answer application forms."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Link(BaseModel):
    label: str
    url: str


class Basics(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[Link] = Field(default_factory=list)


class SkillGroup(BaseModel):
    name: str
    items: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    location: str = ""
    start: str = ""
    end: str = ""  # empty means "Present"
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str = ""
    field: str = ""
    start: str = ""
    end: str = ""
    details: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    url: str = ""


class Resume(BaseModel):
    basics: Basics = Field(default_factory=Basics)
    summary: str = ""
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)

    def plain_text(self) -> str:
        """Readable text used for prompts and for checking what the resume already claims."""
        lines = [self.basics.name, self.summary]
        for group in self.skills:
            lines.append(f"{group.name}: {', '.join(group.items)}")
        for exp in self.experience:
            lines.append(f"{exp.title} at {exp.company} ({exp.start} - {exp.end or 'Present'})")
            lines.extend(f"- {b}" for b in exp.bullets)
        for edu in self.education:
            lines.append(f"{edu.degree} {edu.field}, {edu.institution} {edu.end}".strip())
            lines.extend(f"- {d}" for d in edu.details)
        for proj in self.projects:
            lines.append(f"Project {proj.name}: {proj.description}")
            lines.extend(f"- {b}" for b in proj.bullets)
        lines.extend(f"Certification: {c}" for c in self.certifications)
        return "\n".join(line for line in lines if line)


DECLINE = "Decline to self-identify"


class EEO(BaseModel):
    """Voluntary self-identification answers, entered once and reused."""

    gender: str = DECLINE
    race: str = DECLINE
    hispanic_latino: str = DECLINE
    veteran_status: str = DECLINE
    disability_status: str = DECLINE
    sexual_orientation: str = DECLINE
    pronouns: str = ""


class Profile(BaseModel):
    first_name: str = ""
    last_name: str = ""
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    postal_code: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    website_url: str = ""
    current_company: str = ""
    current_title: str = ""
    years_experience: int | None = None
    # Countries you may work in without sponsorship, e.g. ["United States"].
    authorized_countries: list[str] = Field(default_factory=list)
    requires_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None
    notice_period: str = ""
    earliest_start_date: str = ""
    salary_expectation: str = ""
    how_did_you_hear: str = "Company careers page"
    eeo: EEO = Field(default_factory=EEO)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def location(self) -> str:
        return ", ".join(p for p in (self.city, self.state, self.country) if p)

    def missing_essentials(self) -> list[str]:
        """Fields nearly every application needs; the dashboard asks for these first."""
        required = {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "city": self.city,
            "country": self.country,
            "authorized_countries": self.authorized_countries,
            "requires_sponsorship": self.requires_sponsorship,
        }
        return [name for name, value in required.items() if value in ("", None, [])]
