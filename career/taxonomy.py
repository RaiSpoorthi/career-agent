"""Dictionary of skills/tools that appear in job descriptions.

Used two ways:
  * offline keyword matching between a job description and the profile (free);
  * the truthfulness guard, which spots tools/skills inside rewritten bullets.

Each term: canonical name -> (category, [regex patterns]). Patterns are matched
case-insensitively. Categories steer the security-vs-software resume choice.
"""

from __future__ import annotations

import re
from functools import lru_cache

SEC, SW, DATA, GEN = "security", "software", "data", "general"


def _w(*words: str) -> list[str]:
    """Plain words/phrases -> word-boundary regexes."""
    return [r"(?<![\w/.])" + re.escape(w) + r"(?![\w])" for w in words]


TERMS: dict[str, tuple[str, list[str]]] = {
    # ---------------- identity & access ----------------
    "Identity & Access Management": (SEC, _w("identity and access management", "identity & access management",
                                             "identity access management", "iam")),
    "Zero Trust": (SEC, _w("zero trust", "zero-trust", "ztna")),
    "Azure AD": (SEC, _w("azure ad", "azure active directory", "entra id", "microsoft entra", "aad")),
    "Active Directory": (SEC, [r"(?<!azure )(?<![\w])active directory(?![\w])", *_w("ad ds", "ldap")]),
    "Privileged Identity Management": (SEC, _w("privileged identity management", "pim")),
    "Privileged Access Management": (SEC, _w("privileged access management", "pam")),
    "SSO/MFA": (SEC, _w("sso", "single sign-on", "single sign on", "mfa", "multi-factor authentication",
                        "multi factor authentication", "2fa")),
    "RBAC": (SEC, _w("rbac", "role-based access control", "role based access control")),
    "Access reviews": (SEC, _w("access review", "access reviews", "access certification", "recertification")),
    "Identity lifecycle management": (SEC, _w("identity lifecycle", "joiner mover leaver", "jml", "provisioning",
                                             "deprovisioning", "user lifecycle")),
    "Least privilege": (SEC, _w("least privilege", "least-privilege")),
    "Segregation of Duties": (SEC, _w("segregation of duties", "separation of duties", "sod")),
    "SAML/OAuth": (SEC, _w("saml", "oauth", "oauth2", "openid connect", "oidc")),
    "Okta": (SEC, _w("okta")),
    "SailPoint": (SEC, _w("sailpoint")),
    "CyberArk": (SEC, _w("cyberark")),
    "Ping Identity": (SEC, _w("ping identity", "pingfederate")),
    "Saviynt": (SEC, _w("saviynt")),
    # ---------------- security operations ----------------
    "SIEM": (SEC, _w("siem")),
    "Splunk": (SEC, _w("splunk")),
    "Microsoft Sentinel": (SEC, _w("microsoft sentinel", "azure sentinel", "sentinel")),
    "QRadar": (SEC, _w("qradar")),
    "SOAR": (SEC, _w("soar")),
    "EDR": (SEC, _w("edr", "xdr", "endpoint detection")),
    "CrowdStrike": (SEC, _w("crowdstrike", "falcon")),
    "Microsoft Defender": (SEC, _w("microsoft defender", "defender for endpoint")),
    "SOC": (SEC, [r"(?<![\w])soc(?![\w])(?!\s?2)", *_w("security operations center", "security operations")]),
    "Incident response": (SEC, _w("incident response", "incident handling", "incident management")),
    "Threat Detection": (SEC, _w("threat detection", "detection engineering")),
    "Threat hunting": (SEC, _w("threat hunting")),
    "Threat intelligence": (SEC, _w("threat intelligence", "cti")),
    "MITRE ATT&CK": (SEC, _w("mitre att&ck", "mitre attack", "att&ck")),
    "Malware analysis": (SEC, _w("malware analysis", "reverse engineering")),
    "Digital forensics": (SEC, _w("digital forensics", "forensics", "memory forensics")),
    "Log analysis": (SEC, _w("log analysis", "log monitoring")),
    # ---------------- vuln / appsec / network ----------------
    "Vulnerability Management": (SEC, _w("vulnerability management", "vulnerability assessment",
                                         "vulnerability scanning", "vapt")),
    "Qualys VMDR": (SEC, _w("qualys")),
    "Tenable/Nessus": (SEC, _w("tenable", "nessus")),
    "Penetration Testing": (SEC, _w("penetration testing", "pen testing", "pentesting", "ethical hacking")),
    "Web application security": (SEC, _w("application security", "appsec", "web application security",
                                         "owasp top 10", "owasp")),
    "Burp Suite": (SEC, _w("burp suite", "burp")),
    "OWASP ZAP": (SEC, _w("owasp zap", "zap")),
    "Nmap": (SEC, _w("nmap")),
    "Wireshark": (SEC, _w("wireshark")),
    "Kali Linux": (SEC, _w("kali linux", "kali")),
    "Metasploit": (SEC, _w("metasploit")),
    "Network micro-segmentation": (SEC, _w("micro-segmentation", "microsegmentation", "network segmentation")),
    "Guardicore": (SEC, _w("guardicore")),
    "Firewalls": (SEC, _w("firewall", "firewalls", "palo alto", "fortinet", "fortigate", "checkpoint")),
    "Networking (TCP/IP)": (SEC, _w("tcp/ip", "networking", "dns", "dhcp", "subnetting", "osi model")),
    "Routing and switching": (SEC, _w("routing", "switching", "bgp", "ospf", "vlan")),
    "VPN": (SEC, _w("vpn", "ipsec")),
    "YARA": (SEC, _w("yara")),
    "Sysmon": (SEC, _w("sysmon")),
    "Volatility": (SEC, _w("volatility framework")),
    # ---------------- governance & certs ----------------
    "ISO 27001": (SEC, _w("iso 27001", "iso/iec 27001")),
    "NIST": (SEC, _w("nist", "nist csf", "nist 800-53")),
    "SOC 2": (SEC, [r"(?<![\w])soc\s?2(?![\w])"]),
    "GDPR": (SEC, _w("gdpr")),
    "HIPAA": (SEC, _w("hipaa")),
    "PCI DSS": (SEC, _w("pci dss", "pci-dss", "pci")),
    "Compliance": (SEC, _w("compliance", "audit", "audits", "governance, risk", "grc")),
    "Risk assessment": (SEC, _w("risk assessment", "risk management")),
    "Security+": (SEC, _w("security+", "comptia security")),
    "CEH": (SEC, _w("ceh", "certified ethical hacker")),
    "CISSP": (SEC, _w("cissp")),
    "CCNA": (SEC, _w("ccna")),
    "AZ-500": (SEC, _w("az-500")),
    "SC-300": (SEC, _w("sc-300")),
    "SC-200": (SEC, _w("sc-200")),
    "Policy as code": (SEC, _w("policy as code", "policy-as-code")),
    "OPA/Rego": (SEC, _w("open policy agent", "opa", "rego")),
    "Checkov": (SEC, _w("checkov")),
    "DevSecOps": (SEC, _w("devsecops")),
    # ---------------- cloud & devops ----------------
    "AWS": (GEN, _w("aws", "amazon web services", "ec2", "s3", "lambda", "aws iam")),
    "Azure": (GEN, [r"(?<![\w])azure(?![\w])(?! ad)(?! active directory)(?! sentinel)", *_w("microsoft azure")]),
    "GCP": (GEN, _w("gcp", "google cloud")),
    "Cloud security": (SEC, _w("cloud security", "cspm", "cnapp")),
    "Terraform": (GEN, _w("terraform")),
    "Infrastructure as Code": (GEN, _w("infrastructure as code", "iac", "cloudformation")),
    "Ansible": (GEN, _w("ansible")),
    "Docker": (GEN, _w("docker", "containers", "containerization")),
    "Kubernetes": (GEN, _w("kubernetes", "k8s", "eks", "aks", "gke")),
    "CI/CD": (GEN, _w("ci/cd", "ci cd", "continuous integration", "continuous delivery", "continuous deployment",
                      "pipelines")),
    "GitHub Actions": (GEN, _w("github actions")),
    "Jenkins": (GEN, _w("jenkins")),
    "Linux": (GEN, _w("linux", "unix", "ubuntu", "rhel")),
    "Bash": (GEN, _w("bash", "shell scripting")),
    "PowerShell": (GEN, _w("powershell")),
    "Monitoring": (GEN, _w("prometheus", "grafana", "datadog", "dynatrace", "observability")),
    "Dynatrace": (GEN, _w("dynatrace")),
    "Rubrik": (GEN, _w("rubrik")),
    "ServiceNow": (GEN, _w("servicenow", "itsm")),
    "Jira": (GEN, _w("jira")),
    "ITIL": (GEN, _w("itil")),
    "Automation": (GEN, _w("automation", "automate", "scripting")),
    # ---------------- software ----------------
    "Python": (GEN, _w("python")),
    "JavaScript": (SW, _w("javascript", "js", "es6")),
    "TypeScript": (SW, _w("typescript", "ts")),
    "Java": (SW, _w("java", "spring boot", "spring")),
    "C++": (SW, [r"(?<![\w])c\+\+(?![\w])"]),
    "C#": (SW, [r"(?<![\w])c#(?![\w])", *_w(".net", "dotnet")]),
    "Go": (SW, _w("golang")),
    "SQL": (DATA, _w("sql", "t-sql", "pl/sql")),
    "React": (SW, _w("react", "react.js", "reactjs")),
    "Node.js": (SW, _w("node.js", "nodejs", "node js", "node")),
    "Express": (SW, _w("express", "express.js")),
    "Next.js": (SW, _w("next.js", "nextjs")),
    "Angular": (SW, _w("angular")),
    "Vue": (SW, _w("vue", "vue.js")),
    "HTML/CSS": (SW, _w("html", "css", "html5", "css3", "tailwind")),
    "REST APIs": (SW, _w("rest", "restful", "rest api", "rest apis", "api", "apis", "api integration")),
    "GraphQL": (SW, _w("graphql")),
    "Microservices": (SW, _w("microservices", "microservice")),
    "MongoDB": (SW, _w("mongodb", "mongo")),
    "PostgreSQL": (SW, _w("postgresql", "postgres")),
    "MySQL": (SW, _w("mysql")),
    "Redis": (SW, _w("redis")),
    "Git": (SW, _w("git", "github", "gitlab", "version control")),
    "Postman": (SW, _w("postman")),
    "API documentation": (SW, _w("api documentation", "swagger", "openapi")),
    "Unit testing": (SW, _w("unit testing", "unit tests", "test automation", "automated testing", "jest", "tdd")),
    "pytest": (SW, _w("pytest")),
    "Code review": (SW, _w("code review", "code reviews", "pull request", "pull requests")),
    "Debugging": (SW, _w("debugging", "troubleshooting", "root cause", "bug fixing", "bug fixes")),
    "Agile": (SW, _w("agile", "scrum", "sprint", "sprints", "kanban")),
    "Data structures & algorithms": (SW, _w("data structures", "algorithms", "dsa")),
    "OOP": (SW, _w("object-oriented", "object oriented", "oop")),
    "System design": (SW, _w("system design")),
    "Flask": (SW, _w("flask")),
    "Django": (SW, _w("django")),
    "FastAPI": (SW, _w("fastapi")),
    "Implementation / onboarding": (SW, _w("implementation", "client onboarding", "customer onboarding",
                                           "integration", "integrations", "configuration")),
    # ---------------- data / ML ----------------
    "Power BI": (DATA, _w("power bi", "powerbi")),
    "Power Automate": (DATA, _w("power automate")),
    "Tableau": (DATA, _w("tableau")),
    "Excel": (DATA, _w("microsoft excel", "ms excel", "advanced excel", "excel")),
    "Pandas": (DATA, _w("pandas", "numpy")),
    "Data analysis": (DATA, _w("data analysis", "data analytics", "analytics", "dashboards", "reporting")),
    "Statistics": (DATA, _w("statistics", "statistical")),
    "Machine learning": (DATA, _w("machine learning", "ml", "deep learning")),
    "TensorFlow": (DATA, _w("tensorflow")),
    "Scikit-learn": (DATA, _w("scikit-learn", "sklearn")),
    "Spark": (DATA, _w("spark", "pyspark", "databricks")),
    "Generative AI": (DATA, _w("generative ai", "genai", "llm", "llms", "large language models")),
}

# Which profile skill/stack names count as evidence for a taxonomy term.
# (Profile names that equal a taxonomy key need no entry here.)
PROFILE_ALIASES: dict[str, list[str]] = {
    "Identity & Access Management": ["IAM", "Identity & Access Management"],
    "SSO/MFA": ["SSO", "MFA", "SSO/MFA"],
    "Privileged Identity Management": ["PIM", "Privileged Identity Management"],
    "Qualys VMDR": ["Qualys VMDR"],
    "Monitoring": ["Dynatrace"],
    "Code review": ["Code review"],
    "Debugging": ["Debugging", "Bug fixing"],
    "Automation": ["Power Automate", "Automation", "PowerShell", "Python"],
    "Digital forensics": ["Memory forensics", "Volatility"],
    "Web application security": ["Web application security", "OWASP Top 10", "OWASP ZAP"],
    "Data analysis": ["Power BI"],
    "Implementation / onboarding": ["Client environment configuration"],
    "Volatility": ["Volatility"],
    "Unit testing": ["Unit testing", "pytest"],
    "REST APIs": ["REST APIs", "Postman", "API documentation"],
    "Git": ["Git", "GitHub Actions"],
    "Linux": ["Linux", "Kali Linux"],
    "Identity lifecycle management": ["Identity lifecycle management", "Identity & Access Management"],
}


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, str, re.Pattern[str]]]:
    return [
        (name, category, re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE))
        for name, (category, patterns) in TERMS.items()
    ]


def find_terms(text: str) -> dict[str, int]:
    """Return {canonical term: occurrence count} for every taxonomy term in text."""
    found: dict[str, int] = {}
    for name, _category, pattern in _compiled():
        hits = len(pattern.findall(text or ""))
        if hits:
            found[name] = hits
    return found


def category(term: str) -> str:
    return TERMS[term][0] if term in TERMS else GEN


def evidence_names(term: str) -> list[str]:
    """Profile names that prove the candidate has `term`."""
    return PROFILE_ALIASES.get(term, [term])
