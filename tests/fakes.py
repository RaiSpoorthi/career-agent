"""A stand-in for Gemini that returns a realistic answer, including some deliberate
fabrications, so tests can prove the guard removes them."""

from __future__ import annotations

from typing import Any

CATS = ["overall", "keyword_match", "skills_match", "experience_relevance", "education_match",
        "project_relevance", "formatting_compatibility", "readability", "impact_statements",
        "action_verbs", "industry_alignment", "recruiter_appeal"]


def fake_answer(fabricate: bool = True) -> dict[str, Any]:
    access = ("Provisioned and deprovisioned access for 900 users across 15+ applications with SailPoint"
              if fabricate else
              "Administered joiner/mover/leaver access for 500+ users across 15+ enterprise applications in Azure AD, "
              "reducing access incidents by 25%.")
    return {
        "company": "Acme Security",
        "role": "IAM Analyst",
        "step1_ats_before": {c: 70 for c in CATS},
        "step2_job_analysis": {
            "required_skills": ["Azure AD", "Active Directory", "RBAC", "PIM"],
            "preferred_skills": ["Okta", "CyberArk"], "tools_platforms": ["ServiceNow", "SailPoint"],
            "certifications": [], "responsibilities": ["Provisioning", "Access reviews"],
            "soft_skills": ["Communication"], "industry_keywords": ["Zero Trust"], "seniority": "Entry level",
        },
        "step3_gaps": {
            "strong_matches": ["Azure AD", "Access reviews"], "partial_matches": ["SIEM"],
            "missing_keywords": ["SailPoint", "Okta"], "missing_skills": ["CyberArk"],
            "weak_bullets": ["helix.api: not relevant to IAM"], "missing_industry_terms": ["SOX"],
        },
        "steps4to7_tailored_resume": {
            "headline": "IAM Analyst | Azure AD | Zero Trust | Access Governance" if not fabricate
            else "IAM Analyst | Okta & SailPoint Specialist",
            "experience": [
                {"id": "northwind", "bullets": [
                    {"source_id": "northwind.access", "text": access},
                    {"source_id": "northwind.pim",
                     "text": "Managed Privileged Identity Management (PIM) in Azure, quarterly access reviews "
                             "and Segregation of Duties (SoD) role governance using Saviynt."},
                    {"source_id": "northwind.tickets",
                     "text": "Resolved 70+ monthly SSO, MFA and identity lifecycle tickets in ServiceNow, "
                             "maintaining 98% SLA compliance."},
                    {"source_id": "northwind.automation",
                     "text": ("Led a team that automated SLA reporting" if fabricate else
                     "Automated SLA notifications with Power Automate and scripted account recovery in PowerShell "
                     "and Python, with Power BI SLA dashboards.")},
                ]},
                {"id": "helix", "bullets": [
                    {"source_id": "helix.features", "text": "Resolve bugs and ship feature enhancements on a hospital "
                     "OPD platform using JavaScript, TypeScript, React and Node.js, working from Jira tickets."}]},
            ],
            "projects": [
                {"id": "iac", "bullets": [
                    {"source_id": "iac.pipeline", "text": "Built a DevSecOps pipeline in Terraform and Python that "
                     "detects and auto-remediates drift across 10 AWS resources, enforcing least-privilege IAM."}]},
                {"id": "made_up_project", "bullets": [{"source_id": "x", "text": "Fake project bullet text here"}]},
            ],
            "skills": {"security": ["Identity & Access Management", "Azure AD", "Zero Trust", "Okta", "SIEM"],
                       "tools": ["Saviynt", "ServiceNow"]},
        },
        "step8_ats_after": {c: 84 for c in CATS},
        "step9_recruiter_review": {
            "strengths": ["Real IAM internship"], "weaknesses": ["No SailPoint"],
            "top_matched_keywords": ["Azure AD"], "top_missing_keywords": ["SailPoint"],
            "first_impression_30s": "Strong entry-level IAM profile.",
            "probability_percent": {"ats_pass": 80, "shortlist": 55, "interview": 35},
        },
        "learning_gaps": [{"skill": "SailPoint", "why_it_matters": "Named in the posting", "priority": "high"}],
        "cover_note": ("I administered access for 500+ users at Northwind using Azure AD and Saviynt."
                       if not fabricate else "I have 3 years of Okta experience."),
    }


class FakeLLM:
    """First answer fabricates; the repair answer is clean (like a model that fixes itself)."""

    def __init__(self, answers: list[dict[str, Any]] | None = None):
        self.answers = answers or [fake_answer(True), fake_answer(False)]
        self.calls: list[str] = []
        self.last_model = "fake-gemini"

    def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        self.calls.append(prompt)
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
