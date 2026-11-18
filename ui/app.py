"""Local dashboard (http://127.0.0.1:5055). Runs only on your computer.

Start it with:  python jobs.py ui
Every action button runs the same jobs.py command you could type yourself, so the
tracker, activity log and files stay identical whichever way you use it.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from jinja2 import DictLoader

from career.profile import ROOT, Profile, personal_file, profile_path
from career.tracker import DEFAULT_SETTINGS, STATUSES, SUBMITTED, Tracker

PACKET_FILES = {"resume.pdf", "resume.docx", "answers.md", "report.md", "form_filled.png", "submitted.png"}
EDITABLE = {"sources": "config/sources.yaml", "answers": "config/answers.yaml"}
SETTING_HELP = {
    "apply_mode": ("How applications are sent", ["off", "review", "auto"],
                   "review: the agent fills each form and you press Submit. auto: it submits when every required "
                   "question is answered, and hands you the rest. off: only find jobs and tailor resumes."),
    "min_keyword_score": ("Minimum keyword match (%)", None, "Jobs below this are listed but never processed "
                          "automatically."),
    "tailor_fits": ("Tailor resumes automatically for", ["APPLY+REVIEW", "APPLY"],
                    "APPLY+REVIEW: also jobs with something to check first (they wait under Needs you with the "
                    "reason, and are never submitted for you). APPLY: only jobs that meet every rule."),
    "max_packets_per_run": ("Resumes tailored per run", None, "Each one is a Gemini call. The free tier has a "
                            "daily limit."),
    "ats_target": ("ATS target", None, "When Gemini's ATS estimate is below this, one more pass tries to raise it "
                   "using only what your profile backs (one extra Gemini call). 0 = off."),
    "gemini_gap_seconds": ("Pause between Gemini calls (seconds)", None, ""),
    "max_applications_per_day": ("Automatic submissions per day", None, "A low number keeps this a personal job "
                                 "search, not a bulk sender."),
    "use_gemini": ("Tailor resumes with Gemini", ["on", "off"], "off: use your base resume unchanged."),
    "market_pay": ("Expected salary from market pay", ["on", "off"],
                   "on: Gemini estimates the usual pay for each role at your level (one extra call); the expected-"
                   "salary answer uses it, never below your own floor, with \"(open to discussion)\"."),
    "tick_consent_boxes": ("Tick required privacy/consent boxes for me", ["off", "on"],
                           "off: a form with a required consent box is handed to you."),
    "headless": ("Hide the browser during automatic runs", ["off", "on"], ""),
    "handoff_wait_minutes": ("Minutes the browser stays open when you take over", None, ""),
}
LINK_HELP = {"supported": "a public form the agent can fill in.",
             "unsupported": "a company site the agent opens for you to fill in.",
             "blocked": "a site that needs your account, so it is never opened. Apply there with the packet.",
             "none": "none saved. Apply with the packet wherever you found the job."}
FIT_ORDER = {"APPLY": 0, "REVIEW": 1, "": 2, "SKIP": 3}
ACTIVE = ("awaiting-human", "packet-ready", "discovered", "needs-description")
JOB_TABS = {  # Jobs page tabs: label, statuses
    "open": ("Open", ACTIVE),
    "applied": ("Applied", ("completed-manual", "applied-auto", "interview", "offer", "rejected", "withdrawn")),
    "skipped": ("Skipped", ("skipped",)),
}


def _question_label(pattern: str) -> str:
    """'current (ctc|salary)' -> 'current ctc / salary': a readable label for a question in config/answers.yaml."""
    return re.sub(r"\s+", " ", re.sub(r"[()?^$\\]", " ", pattern.replace("|", " / "))).strip().capitalize()


def _last_scheduled_run(root: Path) -> str:
    """'Last automatic run: <when>' from data/scheduled.log (written by run_scheduled.pyw), or ''."""
    try:
        text = (root / "data" / "scheduled.log").read_text(encoding="utf-8")
    except OSError:
        return ""
    starts = re.findall(r"===== (\d{4}-\d\d-\d\d \d\d:\d\d) scheduled run", text)
    return f"Last automatic run: {starts[-1]}" if starts else ""


def spawn_cli(root: Path, *args: str) -> subprocess.Popen:
    """Start a long jobs.py command (a full run, or a browser takeover) without blocking the page."""
    return subprocess.Popen([sys.executable, "-u", str(root / "jobs.py"), *args], cwd=root, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")


def run_cli(root: Path, *args: str, stdin: str | None = None, timeout: int = 600) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(root / "jobs.py"), *args], cwd=root, input=stdin, text=True,
                          capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout + proc.stderr).strip()


TEMPLATES = {
    "base.html": """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{% block title %}Career Agent{% endblock %}</title>
<style>
:root{--ink:#1d2433;--muted:#667085;--line:#e4e7ec;--bg:#f7f8fa;--card:#fff;--accent:#1f4e79;
--apply:#067647;--review:#b54708;--skip:#b42318;--chip:#eef2f6}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 24px;display:flex;gap:24px;align-items:center}
header b{font-size:16px;color:var(--accent)}header a{color:var(--muted);text-decoration:none;font-weight:500}
header a.on,header a:hover{color:var(--ink)}main{max-width:1180px;margin:24px auto;padding:0 24px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 12px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:16px}
.grid{display:grid;gap:16px}.g4{grid-template-columns:repeat(4,1fr)}.g2{grid-template-columns:1fr 1fr}
.stat b{display:block;font-size:24px}.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums}
a{color:var(--accent)}.chip{display:inline-block;padding:1px 8px;border-radius:99px;background:var(--chip);font-size:12px}
.APPLY{background:#dcfae6;color:var(--apply)}.REVIEW{background:#fef0c7;color:var(--review)}.SKIP{background:#fee4e2;color:var(--skip)}
button,.btn{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:7px 14px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
.btn.ghost,button.ghost{background:#fff;color:var(--accent);border:1px solid var(--line)}
input,select,textarea{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px}
textarea{width:100%;min-height:180px}form.inline{display:inline-flex;gap:8px;align-items:center;margin:0}
.flash{background:#eff4ff;border:1px solid #c7d7fe;border-radius:8px;padding:10px 14px;margin-bottom:16px;white-space:pre-wrap;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;margin:0}
.badge{background:var(--skip);color:#fff;border-radius:99px;padding:0 7px;font-size:11px}
.alert{background:#fffaeb;border:1px solid #fedf89;border-radius:8px;padding:8px 14px;margin-bottom:10px;display:flex;justify-content:space-between;gap:12px;align-items:center}
.warn{color:var(--review)}.run{color:var(--accent)}label.set{display:grid;grid-template-columns:300px 160px 1fr;gap:12px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)}
textarea.code{font:12px/1.5 ui-monospace,Consolas,monospace;min-height:260px}
.kw span{margin:0 4px 4px 0}.note{font-size:12px;color:var(--muted)}
.g3{grid-template-columns:repeat(3,1fr)}.g5{grid-template-columns:repeat(5,1fr)}.pick h3{font-size:17px;margin:6px 0 2px}.pick .learn{margin-top:10px;font-size:13px}
.bar{height:6px;background:var(--chip);border-radius:99px;overflow:hidden;margin:6px 0 2px}.bar span{display:block;height:100%;background:var(--accent);border-radius:99px}
.skill{display:grid;grid-template-columns:minmax(150px,1fr) minmax(130px,.8fr) 2fr;gap:16px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.skill:last-child{border-bottom:0}.skill b{font-size:14px}.skill .bar{margin:0 0 2px}
.quick{background:#dcfae6;color:var(--apply)}.new{background:#eff4ff;color:var(--accent)}
.steps{display:flex;gap:24px;flex-wrap:wrap;counter-reset:s}.steps div{flex:1;min-width:200px;font-size:13px}
.steps div::before{counter-increment:s;content:counter(s);display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:var(--accent);color:#fff;font-size:12px;font-weight:700;margin-right:8px}
details summary{cursor:pointer;color:var(--accent);font-weight:600}details .chip{margin:8px 6px 0 0}
.glance .facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:12px}
.glance .facts div{background:var(--bg);border-radius:8px;padding:8px 12px}.glance .facts span{display:block;font-size:12px;color:var(--muted)}
.glance h3{font-size:14px;margin:8px 0 4px}.glance ul{margin:0;padding-left:18px}.glance li{margin:3px 0}
.glance .chip{margin:0 4px 4px 0}
.tabs{display:flex;gap:4px;margin-bottom:12px;border-bottom:1px solid var(--line)}
.tabs a{padding:8px 14px;color:var(--muted);text-decoration:none;font-weight:600;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a.on{color:var(--ink);border-bottom-color:var(--accent)}
label.field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}label.field input{width:100%}
iframe.preview{width:100%;height:760px;border:1px solid var(--line);border-radius:8px;background:#fff}
@media(max-width:800px){.g4,.g2,.g5{grid-template-columns:1fr 1fr}.g3{grid-template-columns:1fr}.skill{grid-template-columns:1fr}
.grid[style*="2fr 1fr"]{grid-template-columns:1fr!important}}
</style></head><body>
<header><b>Career Agent</b>
<a href="{{ url_for('dashboard') }}" class="{{ 'on' if active=='home' }}">Dashboard</a>
<a href="{{ url_for('needs') }}" class="{{ 'on' if active=='needs' }}">Needs you{% if waiting %} <span class="badge">{{ waiting }}</span>{% endif %}</a>
<a href="{{ url_for('jobs') }}" class="{{ 'on' if active=='jobs' }}">Jobs</a>
<a href="{{ url_for('add_job') }}" class="{{ 'on' if active=='add' }}">Add job</a>
<a href="{{ url_for('gaps') }}" class="{{ 'on' if active=='gaps' }}">Learn next</a>
<a href="{{ url_for('activity') }}" class="{{ 'on' if active=='log' }}">Logs</a>
<a href="{{ url_for('settings') }}" class="{{ 'on' if active=='settings' }}">Settings</a>
<span style="margin-left:auto" class="note">apply mode: <b>{{ mode }}</b></span></header>
<main>{% if task %}<div class="flash" id="task"><b>{{ task.name }}</b> is running…
<span id="tasklog">{{ task.tail }}</span></div>{% endif %}
{% for b in browsers %}<div class="flash"><b>{{ b.name }}</b> {{ 'finished:' if b.done else 'is working… (this page refreshes when it is done)' }}
<span style="white-space:pre-wrap">{{ b.tail or 'Starting…' }}</span></div>{% endfor %}
{% if task or browsers|rejectattr('done')|list %}
<script>const t=setInterval(async()=>{try{const r=await (await fetch('{{ url_for('task_status') }}')).json();
const log=document.getElementById('tasklog');if(log)log.textContent='\n'+r.tail;
if(!r.running){clearInterval(t);location.reload()}}catch(e){}},2000)</script>{% endif %}
{% for n in notes %}<div class="alert"><span>{{ n.message }}</span>
<form method="post" action="{{ url_for('dismiss', note_id=n.id) }}" class="inline"><button class="ghost">Dismiss</button></form></div>{% endfor %}
{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}{% block body %}{% endblock %}</main>
</body></html>""",

    "dashboard.html": """{% extends 'base.html' %}{% block body %}
<div class="row" style="justify-content:space-between;margin-bottom:16px">
 <div><h1>Today</h1><div class="muted">{{ today }} · everything here is free; resumes are tailored with your free Gemini key</div></div>
 <div class="row">
  <a class="btn" href="{{ url_for('add_job') }}">Apply to a job I found</a>
  <form method="post" action="{{ url_for('run_agent') }}" class="inline"><button class="ghost">Run agent now</button></form>
  <form method="post" action="{{ url_for('discover') }}" class="inline"><button class="ghost">Search career pages</button></form>
  <form method="post" action="{{ url_for('ingest') }}" class="inline"><button class="ghost">Read alert emails</button></form>
 </div></div>
<div class="card"><div class="row" style="justify-content:space-between">
 <h2>Since your last check</h2><span class="note">{{ last_auto or 'No automatic run yet' }}</span></div>
{% if recent %}<table>{% for r in recent %}<tr><td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.role }}</a>
<div class="note">{{ r.company }} · {{ r.location }}</div></td>
<td>{{ 'submitted by the agent' if r.status == 'applied-auto' else 'submitted by you' }}</td>
<td class="note">{{ r.date_applied or r.updated[:10] }}</td></tr>{% endfor %}</table>
{% else %}<div class="muted">No applications submitted in the last day and a half.</div>{% endif %}
{% if counts['awaiting-human'] %}<div style="margin-top:12px"><a class="btn" href="{{ url_for('needs') }}">Finish {{ counts['awaiting-human'] }} waiting for you →</a></div>{% endif %}
</div>
<div class="grid g4">
 <div class="card stat"><span>Needs you</span><b><a href="{{ url_for('needs') }}">{{ counts['awaiting-human'] }}</a></b></div>
 <div class="card stat"><span>New matches</span><b>{{ counts['discovered'] }}</b></div>
 <div class="card stat"><span>Submitted (auto / by you)</span><b>{{ counts['applied-auto'] }} / {{ counts['completed-manual'] }}</b></div>
 <div class="card stat"><span>Interviews</span><b>{{ counts['interview'] }}</b></div>
</div>
<div class="grid g2">
<div class="card"><h2>Packets ready</h2>
{% if ready %}<table>{% for r in ready %}<tr><td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.role }}</a><div class="note">{{ r.company }}</div></td>
<td class="num">{{ r.ats_after or '-' }}<div class="note">ATS</div></td><td class="num">{{ r.shortlist_pct or '-' }}{{ '%' if r.shortlist_pct }}<div class="note">shortlist est.</div></td></tr>{% endfor %}</table>
{% else %}<div class="muted">No packets waiting. Pick a job below and press "Generate packet".</div>{% endif %}</div>
<div class="card"><h2>Best new matches</h2>
{% if best %}<table>{% for r in best %}<tr><td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.role }}</a><div class="note">{{ r.company }} · {{ r.location }} · pay {{ r.salary or 'not stated' }}</div></td>
<td><span class="chip {{ r.fit }}">{{ r.fit or '?' }}</span></td><td class="num">{{ r.keyword_score }}%</td></tr>{% endfor %}</table>
{% else %}<div class="muted">Nothing new. Add companies in config/sources.yaml, drop alert emails in inbox/, or add a job by hand.</div>{% endif %}</div>
</div>
{% if needs %}<div class="card"><h2>Need the job description ({{ needs|length }})</h2>
<div class="muted" style="margin-bottom:8px">Alert emails and some career pages only give a title and link. Open the listing, copy the full description, and paste it on the job page.</div>
<table>{% for r in needs %}<tr><td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.role }}</a></td><td>{{ r.source }}</td><td><a href="{{ r.url }}" target="_blank" rel="noopener">open listing</a></td></tr>{% endfor %}</table></div>{% endif %}
<div class="card"><h2>Recent activity</h2><pre>{{ log }}</pre></div>
{% endblock %}""",

    "needs.html": """{% extends 'base.html' %}{% block body %}
<h1>Needs you</h1><div class="muted" style="margin-bottom:16px">Applications the agent could not finish alone. "Take over" opens the form in a browser on this computer with everything it can answer already filled in; you finish and press Submit.</div>
{% for r in rows %}<div class="card"><div class="row" style="justify-content:space-between">
<div><a href="{{ url_for('job', job_id=r.id) }}"><b>{{ r.role }}</b></a> · {{ r.company }} <span class="note">· {{ r.location }} · pay {{ r.salary or 'not stated' }} · {{ r.id }} · keyword {{ r.keyword_score }}%</span>
{% if r.glance %}<div class="note" style="margin-top:4px">{{ r.glance }}</div>{% endif %}
<div class="warn" style="margin-top:4px">{{ r.handoff_reason or 'Waiting for you.' }}</div></div>
<div class="row">
{% if r.automatable %}<form method="post" action="{{ url_for('takeover', job_id=r.id) }}" class="inline"><button>Take over</button></form>
<a class="btn ghost" href="{{ r.apply_url or r.url }}" target="_blank" rel="noopener"
 onclick="setTimeout(()=>location.href='{{ url_for('job', job_id=r.id) }}#answers',300)"
 title="Opens the application form in a new tab of this browser, and shows your answers here to copy">Apply in my browser ↗</a>
{% else %}<a class="btn" href="{{ r.url }}" target="_blank" rel="noopener">Open listing ↗</a>{% endif %}
{% if r.output_dir %}<a class="btn ghost" href="{{ url_for('packet_file', job_id=r.id, name='resume.pdf') }}" target="_blank">Resume</a>
<a class="btn ghost" href="{{ url_for('job', job_id=r.id) }}#change">Change resume</a>
<a class="btn ghost" href="{{ url_for('packet_file', job_id=r.id, name='answers.md') }}" target="_blank">Answers</a>{% endif %}
<form method="post" action="{{ url_for('status', job_id=r.id) }}" class="inline"><input type="hidden" name="status" value="completed-manual"><input type="hidden" name="back" value="needs"><button class="ghost">I submitted it</button></form>
<form method="post" action="{{ url_for('status', job_id=r.id) }}" class="inline"><input type="hidden" name="status" value="skipped"><input type="hidden" name="back" value="needs"><button class="ghost">Skip</button></form>
</div></div></div>
{% else %}<div class="card muted">Nothing is waiting for you.</div>{% endfor %}
{% endblock %}""",

    "logs.html": """{% extends 'base.html' %}{% block body %}
<div class="row" style="justify-content:space-between;margin-bottom:12px"><h1>Logs</h1>
<form class="inline" method="get"><select name="level"><option value="">Everything</option>{% for l in ['run','warn','info'] %}<option {{ 'selected' if l==level }}>{{ l }}</option>{% endfor %}</select><button class="ghost">Filter</button></form></div>
<div class="card" style="padding:0;overflow-x:auto"><table><tr><th>When</th><th>Job</th><th>What happened</th></tr>
{% for e in events %}<tr><td class="note" style="white-space:nowrap">{{ e.ts }}</td><td>{% if e.job_id %}<a href="{{ url_for('job', job_id=e.job_id) }}">{{ e.job_id }}</a>{% endif %}</td><td class="{{ e.level }}">{{ e.message }}</td></tr>
{% else %}<tr><td colspan="3" class="muted">No activity yet.</td></tr>{% endfor %}</table></div>
{% endblock %}""",

    "settings.html": """{% extends 'base.html' %}{% block body %}
<h1>Settings</h1>
<div class="card"><h2>Agent</h2><form method="post" action="{{ url_for('save_settings') }}">
{% for key, (title, choices, help) in fields.items() %}<label class="set"><span><b>{{ title }}</b></span>
{% if choices %}<select name="{{ key }}" id="{{ key }}">{% for c in choices %}<option {{ 'selected' if values[key]==c }}>{{ c }}</option>{% endfor %}</select>
{% else %}<input name="{{ key }}" id="{{ key }}" value="{{ values[key] }}" inputmode="numeric">{% endif %}<span class="note">{{ help }}</span></label>{% endfor %}
<p><button>Save settings</button></p></form></div>
{% for name, f in files.items() %}<div class="card"><h2>{{ f.path }}</h2><div class="note" style="margin-bottom:8px">{{ f.help }}</div>
<form method="post" action="{{ url_for('save_file', name=name) }}"><textarea class="code" name="text" id="file-{{ name }}">{{ f.text }}</textarea><p><button>Save {{ f.path }}</button></p></form></div>{% endfor %}
<div class="card"><h2>Never automated</h2><div class="note">LinkedIn, Naukri, Indeed and other sites that need your account are never opened by the agent, whatever the settings say. Their jobs appear under Needs you with the packet ready.</div></div>
{% endblock %}""",

    "jobs.html": """{% extends 'base.html' %}{% block body %}
<div class="row" style="justify-content:space-between;margin-bottom:12px"><h1>Jobs</h1>
<form class="inline" method="get"><input type="hidden" name="tab" value="{{ tab }}">
<input type="search" name="q" value="{{ q }}" placeholder="Search role, company, location, skill…" style="width:280px" aria-label="Search jobs">
<select name="fit"><option value="">Any fit</option>{% for f in ['APPLY','REVIEW','SKIP'] %}<option {{ 'selected' if f==fit }}>{{ f }}</option>{% endfor %}</select>
<button class="ghost">Search</button>{% if q or fit %}<a class="note" href="{{ url_for('jobs', tab=tab) }}">clear</a>{% endif %}</form></div>
<div class="tabs">{% for key, label in tab_labels.items() %}<a href="{{ url_for('jobs', tab=key, q=q, fit=fit) }}" class="{{ 'on' if key==tab }}">{{ label }} <span class="chip">{{ tab_counts[key] }}</span></a>{% endfor %}</div>
{% if tab == 'applied' %}
<div class="card" style="padding:0;overflow-x:auto"><table>
<tr><th>Role · Company</th><th>Applied on</th><th>Status</th><th>Pay</th><th>ATS</th><th>Packet</th><th>Listing</th></tr>
{% for r in rows %}<tr><td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.role }}</a><div class="note">{{ r.company }} · {{ r.location }} · {{ r.id }}</div></td>
<td>{{ r.date_applied or '-' }}</td>
<td><form method="post" action="{{ url_for('status', job_id=r.id) }}" class="inline"><input type="hidden" name="back" value="applied">
<select name="status" onchange="this.form.submit()" aria-label="Status of {{ r.id }}">{% for s in applied_statuses %}<option {{ 'selected' if s==r.status }}>{{ s }}</option>{% endfor %}</select></form></td>
<td>{% if r.salary %}<b>{{ r.salary }}</b>{% elif r.market_pay %}<span class="note" title="Not stated in the posting; Gemini's estimate of usual pay at your level">~{{ r.market_pay }} est.</span>{% else %}<span class="note">not stated</span>{% endif %}</td>
<td class="num">{{ r.ats_after or r.ats_score or '-' }}</td>
<td>{% if r.output_dir %}{% for f, label in [('resume.pdf','PDF'),('resume.docx','Word'),('report.md','Report'),('answers.md','Answers')] %}<a href="{{ url_for('packet_file', job_id=r.id, name=f) }}" target="_blank">{{ label }}</a>{{ ' · ' if not loop.last }}{% endfor %}{% else %}-{% endif %}</td>
<td>{% if r.url %}<a href="{{ r.url }}" target="_blank" rel="noopener">open ↗</a>{% else %}-{% endif %}</td></tr>
{% else %}<tr><td colspan="7" class="muted">{{ 'No applied jobs match.' if q or fit else 'Nothing applied yet. Jobs move here when you press "I submitted it" or "Mark applied".' }}</td></tr>{% endfor %}</table></div>
{% else %}
<div class="card" style="padding:0;overflow-x:auto"><table>
<tr><th>ID</th><th>Role · Company</th><th>Pay</th><th>Source</th><th>Fit</th><th>Keywords</th><th>ATS before→after</th><th>Shortlist est.</th><th>Status</th><th>Applied on</th><th>Resume</th></tr>
{% for r in rows %}<tr><td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.id }}</a></td>
<td><a href="{{ url_for('job', job_id=r.id) }}">{{ r.role }}</a><div class="note">{{ r.company }} · {{ r.location }}</div></td>
<td>{% if r.salary %}<b>{{ r.salary }}</b>{% elif r.market_pay %}<span class="note" title="Not stated in the posting; Gemini's estimate of usual pay at your level">~{{ r.market_pay }} est.</span>{% else %}<span class="note">not stated</span>{% endif %}</td>
<td class="note">{{ r.source or 'manual' }}</td><td><span class="chip {{ r.fit }}">{{ r.fit or '?' }}</span></td>
<td class="num">{{ r.keyword_score }}%</td><td class="num">{{ r.ats_before or '-' }} → {{ r.ats_after or r.ats_score or '-' }}</td>
<td class="num">{{ r.shortlist_pct ~ '%' if r.shortlist_pct else '-' }}</td><td><span class="chip">{{ r.status }}</span></td>
<td>{{ r.date_applied or '-' }}</td>
<td>{% if r.output_dir %}<a href="{{ url_for('packet_file', job_id=r.id, name='resume.pdf') }}" target="_blank">PDF</a>{% else %}-{% endif %}</td></tr>
{% else %}<tr><td colspan="11" class="muted">No jobs match.</td></tr>{% endfor %}</table></div>
{% endif %}
<div class="note">Pay is read from the posting; most don't state it. ATS and shortlist numbers are Gemini's estimates, not real probabilities. Keyword % is an exact-match count done on your computer.</div>
{% endblock %}""",

    "job.html": """{% extends 'base.html' %}{% block title %}{{ r.role }}{% endblock %}{% block body %}
<div class="row" style="justify-content:space-between"><div><h1>{{ r.role }}</h1>
<div class="muted">{{ r.company }} · {{ r.location or 'location not stated' }} · {{ r.id }} · from {{ r.source or 'manual' }} · added {{ r.added }}</div></div>
{% if r.url %}<a class="btn ghost" href="{{ r.url }}" target="_blank" rel="noopener">Open job listing ↗</a>{% endif %}</div>
<div class="grid g5" style="margin-top:16px">
<div class="card stat"><span>Fit</span><b><span class="chip {{ r.fit }}" style="font-size:16px">{{ r.fit or '?' }}</span></b></div>
<div class="card stat"><span>Pay</span><b>{% if r.salary %}{{ r.salary }}{% else %}<span class="muted" style="font-size:15px">not stated</span>{% endif %}</b></div>
<div class="card stat"><span>Keyword match</span><b>{{ r.keyword_score }}%</b></div>
<div class="card stat"><span>ATS before → after</span><b>{{ r.ats_before or '-' }} → {{ r.ats_after or r.ats_score or '-' }}</b></div>
<div class="card stat"><span>Shortlist (estimate)</span><b>{{ r.shortlist_pct ~ '%' if r.shortlist_pct else '-' }}</b></div>
</div>
{% if r.notes %}<div class="card"><h2>Check first</h2>{{ r.notes }}</div>{% endif %}
{% if glance %}<div class="card glance"><h2>At a glance</h2>
<div class="facts"><div><span>Experience asked</span><b>{{ glance.years }}</b></div>
<div><span>Pay</span><b>{{ glance.pay if glance.pay != 'not stated' else 'not stated' }}</b></div>
<div><span>Location</span><b>{{ glance.location or 'not stated' }}{{ ' · remote' if glance.remote }}</b></div>
{% if market %}<div><span>Market pay (estimate)</span><b>{{ market.label }}</b></div>{% endif %}
{% if glance.seniority %}<div><span>Level</span><b>{{ glance.seniority }}</b></div>{% endif %}</div>
{% if glance.must_have_skills %}<p><b>Must-have skills:</b> {% for s in glance.must_have_skills %}<span class="chip">{{ s }}</span> {% endfor %}</p>{% endif %}
{% if glance.nice_skills %}<p><b>Nice to have:</b> {% for s in glance.nice_skills %}<span class="chip">{{ s }}</span> {% endfor %}</p>{% endif %}
<div class="kw"><p><b>You have:</b> {% for k in glance.skills_have %}<span class="chip APPLY">{{ k }}</span>{% else %}-{% endfor %}</p>
{% if glance.skills_coursework %}<p><b>Coursework only:</b> {% for k in glance.skills_coursework %}<span class="chip REVIEW">{{ k }}</span>{% endfor %}</p>{% endif %}
<p><b>Missing:</b> {% for k in glance.skills_missing %}<span class="chip SKIP">{{ k }}</span>{% else %}-{% endfor %}</p></div>
<div class="grid g2">{% if glance.duties %}<div><h3>What you'd do</h3><ul>{% for d in glance.duties %}<li>{{ d }}</li>{% endfor %}</ul></div>{% endif %}
{% if glance.required %}<div><h3>What they ask for</h3><ul>{% for d in glance.required %}<li>{{ d }}</li>{% endfor %}</ul></div>{% endif %}
{% if glance.nice %}<div><h3>Bonus points</h3><ul>{% for d in glance.nice %}<li>{{ d }}</li>{% endfor %}</ul></div>{% endif %}</div>
<p class="note">Taken from the posting{{ " and Gemini's reading of it" if glance.must_have_skills }}. Missing skills are never added to your resume; they go to Learn next.</p></div>{% endif %}

{% if r.status == 'needs-description' %}
<div class="card"><h2>Paste the job description</h2>
<div class="muted" style="margin-bottom:8px">Open the listing, copy the full description and paste it here. Then it can be scored and tailored.</div>
<form method="post" action="{{ url_for('attach', job_id=r.id) }}"><textarea name="text" required></textarea><p><button>Save description</button></p></form></div>
{% else %}
<div class="card"><h2>Application packet</h2>
{% if r.output_dir %}
<div class="row">{% for f in files %}<a class="btn ghost" href="{{ url_for('packet_file', job_id=r.id, name=f) }}" target="_blank">{{ f }}</a>{% endfor %}</div>
<p class="note">Upload resume.pdf (or .docx if the site asks). Copy answers from answers.md. Read the resume once: you must be able to explain every line.</p>
{% else %}<p class="muted">Not generated yet.</p>{% endif %}
<div class="row">
{% if r.status not in submitted %}<form method="post" action="{{ url_for('apply_now', job_id=r.id) }}" class="inline"><button>{{ 'Apply now' if r.output_dir else 'Tailor resume and apply' }}</button></form>{% endif %}
<form method="post" action="{{ url_for('packet', job_id=r.id) }}" class="inline"><button class="ghost">{{ 'Regenerate' if r.output_dir else 'Generate' }} packet (Gemini)</button></form>
<form method="post" action="{{ url_for('packet', job_id=r.id) }}" class="inline"><input type="hidden" name="offline" value="1"><button class="ghost">Base resume only (offline)</button></form>
</div>
<p class="note">Application link: {{ link_help }} Apply mode is <b>{{ mode }}</b>{% if mode == 'review' %}: the form opens filled in and you press Submit{% elif mode == 'auto' %}: it submits only when every required question is answered{% else %}: nothing is opened{% endif %}.</p>
</div>
{% if 'resume.pdf' in files %}<div class="card" id="change"><h2>Change this resume</h2>
<div class="note" style="margin-bottom:8px">Tell the agent what to change, in your own words. It rewrites the resume, the
truthfulness guard checks every line, and the ATS score is checked again. It can reorder, reword, choose bullets and skills,
and change the headline. It can't add anything your profile doesn't have, and it tells you when it skips part of a request.</div>
<form method="post" action="{{ url_for('revise', job_id=r.id) }}"><textarea name="instruction" required style="min-height:90px"
placeholder="e.g. Put my internship first, start the headline with Azure AD and access reviews, and use fewer project bullets."></textarea>
<p><button>Apply my changes and re-check ATS</button></p></form>
{% if revisions %}<h2 style="margin-top:8px">What you asked for</h2>
{% for rev in revisions|reverse %}<div style="border-top:1px solid var(--line);padding:10px 0">
<div><b>“{{ rev.instruction }}”</b> <span class="note">· ATS {{ rev.ats_before or '-' }} → <b>{{ rev.ats_after or '-' }}</b> · exact keywords {{ rev.keyword_before }}% → {{ rev.keyword_after }}%</span></div>
{% for c in rev.changes_made %}<div class="note">✓ {{ c }}</div>{% endfor %}
{% for c in rev.not_done %}<div class="note warn">✗ Not done: {{ c }}</div>{% endfor %}
{% if rev.guard_issues %}<details class="note"><summary style="font-weight:400">↺ {{ rev.guard_issues|length }} suggestion(s) from Gemini were changed back by the truthfulness guard</summary>
{% for c in rev.guard_issues %}<div class="note">{{ c }}</div>{% endfor %}</details>{% endif %}
</div>{% endfor %}{% endif %}</div>
<div class="card"><h2>Tailored resume</h2>
<iframe class="preview" src="{{ url_for('packet_file', job_id=r.id, name='resume.pdf') }}" title="Tailored resume preview"></iframe></div>{% endif %}
{% endif %}

{% if r.output_dir and r.status not in submitted %}<div class="card"><h2>Apply</h2>
{% if r.handoff_reason %}<p class="warn">{{ r.handoff_reason }}</p>{% endif %}
{% if automatable %}<div class="row">
<form method="post" action="{{ url_for('takeover', job_id=r.id) }}" class="inline"><button>Take over (opens the filled form)</button></form>
<form method="post" action="{{ url_for('auto_apply', job_id=r.id) }}" class="inline"><button class="ghost">Fill and submit for me</button></form>
<a class="btn ghost" href="{{ r.apply_url or r.url }}" target="_blank" rel="noopener">Apply in my browser ↗</a></div>
<p class="note">"Take over" and "Fill and submit" use the agent's browser. "Fill and submit" stops and hands the form to you if a required question has no answer or a captcha appears.
Some sites reject the agent's browser even after you solve a captcha ("error verifying your application"): then use <b>Apply in my browser</b>, which opens the form in a new tab here, and copy the answers below.</p>
{% else %}<p class="note">This listing is on a site the agent does not automate. Open it and apply with the packet above and the answers below, then mark it applied.</p>{% endif %}
{% if answers %}<h2 id="answers" style="margin-top:14px">Your answers</h2>
{% set form_link = r.apply_url or r.url %}
<div class="row" style="margin-bottom:8px">
{% if form_link %}<a class="btn" href="{{ form_link }}" target="_blank" rel="noopener">1. Open the application form ↗</a>{% endif %}
<form method="post" action="{{ url_for('open_folder', job_id=r.id) }}" class="inline"><button class="ghost">2. Open resume folder</button></form>
<span class="note">3. Copy each answer below into the form, attach resume.pdf, submit, then mark it applied.</span></div>
{% if form_link %}<div class="note" style="margin-bottom:8px">Form: <a href="{{ form_link }}" target="_blank" rel="noopener">{{ form_link }}</a>
<button type="button" class="ghost copy" data-v="{{ form_link }}">Copy link</button></div>{% endif %}
<table>{% for q, a in answers %}<tr><td style="width:30%" class="note">{{ q }}</td><td>{{ a }}</td>
<td style="width:70px"><button type="button" class="ghost copy" data-v="{{ a }}">Copy</button></td></tr>{% endfor %}</table>
{% if cover %}<p style="margin-top:10px"><b>Why are you a good fit?</b> <button type="button" class="ghost copy" data-v="{{ cover }}">Copy</button></p><p>{{ cover }}</p>{% endif %}
<script>document.querySelectorAll('button.copy').forEach(b=>b.addEventListener('click',async()=>{
try{await navigator.clipboard.writeText(b.dataset.v);const t=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=t,1200)}catch(e){}}))</script>{% endif %}
</div>{% endif %}
<div class="grid g2">
<div class="card"><h2>Applied?</h2>
{% if r.date_applied %}<p>Applied on <b>{{ r.date_applied }}</b>.</p>{% endif %}
<form method="post" action="{{ url_for('applied', job_id=r.id) }}" class="inline"><input type="date" name="date" value="{{ r.date_applied or today }}"><button>Mark applied</button></form></div>
<div class="card"><h2>Status</h2>
<form method="post" action="{{ url_for('status', job_id=r.id) }}" class="inline"><select name="status">{% for s in statuses %}<option {{ 'selected' if s==r.status }}>{{ s }}</option>{% endfor %}</select>
<input name="note" placeholder="note (optional)"><button class="ghost">Update</button></form></div></div>

{% if history %}<div class="card"><h2>History</h2><pre>{% for e in history %}{{ e.ts }}  {{ e.message }}
{% endfor %}</pre></div>{% endif %}
<div class="card"><details{{ ' open' if not glance }}><summary><b>Full job description</b></summary><pre style="margin-top:10px">{{ jd }}</pre></details></div>
{% endblock %}""",

    "add.html": """{% extends 'base.html' %}{% block body %}
<h1>Apply to a job you found</h1>
<div class="muted" style="margin-bottom:16px">Paste the link and the full description. The agent scores it and tailors your
resume with Gemini. {{ next_step }}</div>
<div class="grid" style="grid-template-columns:2fr 1fr;align-items:start">
<form method="post" action="{{ url_for('add_job') }}" class="card">
<label class="field"><b>Job link</b><input name="url" type="url" required placeholder="https://job-boards.greenhouse.io/company/jobs/123" value="{{ form.url }}"></label>
<div class="grid g3" style="gap:12px">
<label class="field"><b>Company</b><input name="company" required value="{{ form.company }}"></label>
<label class="field"><b>Role</b><input name="role" required value="{{ form.role }}"></label>
<label class="field"><b>Location</b><input name="location" placeholder="Bengaluru, Remote…" value="{{ form.location }}"></label>
</div>
<label class="field"><b>Job description</b><textarea name="text" required style="min-height:300px" placeholder="Copy the whole description from the listing">{{ form.text }}</textarea></label>
<div class="row"><button>Tailor resume and apply</button><button class="ghost" name="only_save" value="1">Only save and score</button></div>
</form>
<div class="card"><h2>What happens</h2><div class="steps" style="flex-direction:column;gap:12px">
<div>Saved and scored on your computer: keyword match and your fit rules.</div>
<div>Resume tailored for this job (one Gemini call). The truthfulness guard checks every line.</div>
<div>{{ next_step }}</div></div>
<p class="note" style="margin-top:14px"><b>Auto-filled:</b> Greenhouse, Lever, Ashby and Recruitee links.<br>
<b>Opened for you to fill:</b> other company sites.<br><b>Never opened:</b> LinkedIn, Naukri, Indeed and other sites that need
your account. You get the packet to apply yourself.</p>
<p class="note">Change apply mode in <a href="{{ url_for('settings') }}">Settings</a>.</p></div>
</div>
<div class="card"><h2>Save jobs from LinkedIn, Naukri or any job site in one click</h2>
<p>Drag this button to your browser's bookmarks bar (press <b>Ctrl+Shift+B</b> if you don't see the bar):
<a class="btn" title="Drag me to the bookmarks bar" onclick="alert('Drag this button to your bookmarks bar, then click it while viewing a job.');return false"
href="javascript:(function(){var s=String(window.getSelection()).trim();var m=document.querySelector('main');var t=s.length>200?s:((m&amp;&amp;m.innerText)||document.body.innerText);var p=document.title.replace(/^[(][0-9]+[)] */,'').split(/ [|–-] /);window.open('{{ request.host_url }}add?url='+encodeURIComponent(location.href)+'&amp;role='+encodeURIComponent(p[0]||'')+'&amp;company='+encodeURIComponent(p.length>2?p[1]:'')+'&amp;text='+encodeURIComponent(t.slice(0,12000)),'_blank')})()">Save to Career Agent</a></p>
<ol class="note" style="font-size:13px"><li>Open a job on LinkedIn, Naukri or any site, the same way you always do (for example from a LinkedIn alert email).</li>
<li>Click <b>Save to Career Agent</b> in your bookmarks bar. This page opens with the link, title, company and description filled in.
If the description picks up extra page text, select just the description first, then click the bookmark.</li>
<li>Check the fields and press <b>Tailor resume and apply</b>. Jobs from LinkedIn and Naukri are never opened by the agent: you get the tailored packet and apply there yourself.</li></ol>
<p class="note">The bookmark only copies what is on the page you're looking at, when you click it, like copy and paste. The agent never visits LinkedIn or Naukri itself.</p></div>
{% endblock %}""",

    "gaps.html": """{% extends 'base.html' %}{% macro skill_row(s) %}<div class="skill">
<div><b>{{ s.skill }}</b><div class="note">{{ s.area }}</div></div>
<div><div class="bar"><span style="width:{{ s.share }}%"></span></div><div class="note">{{ s.count }} of {{ total }} jobs</div></div>
<div>{% if s.learn %}{{ s.learn }}{% else %}<span class="note">No free course suggested yet</span>{% endif %}</div></div>{% endmacro %}
{% block body %}
<h1>Learn next</h1>
<div class="muted" style="margin-bottom:16px">Skills your {{ total }} saved job{{ 's' if total != 1 }} ask for that your resume doesn't show yet.
Learning one never changes your resume by itself: it appears only after you add it to baseline.yaml.</div>
{% if not total %}<div class="card muted">No jobs saved yet. Run the agent or add a job, and this page fills in.</div>
{% else %}
{% if picks %}<h2>Start here</h2><div class="grid g3">{% for s in picks %}<div class="card pick">
<span class="chip {{ 'quick' if s.kind == 'partial' else 'new' }}">{{ 'Quick win: you studied this' if s.kind == 'partial' else 'New skill' }}</span>
<h3>{{ s.skill }}</h3><div class="note">Asked by {{ s.count }} of {{ total }} jobs ({{ s.share }}%) · {{ s.area }}</div>
<div class="bar"><span style="width:{{ s.share }}%"></span></div>
<div class="learn">{% if s.learn %}<b>How:</b> {{ s.learn }}{% else %}<span class="note">No free course suggested yet</span>{% endif %}</div>
</div>{% endfor %}</div>{% endif %}
{% for title, help, kind in [('Quick wins', 'You know these from coursework. Build something real, then raise its level in baseline.yaml.', 'partial'),
                             ('New skills', 'Not on your profile yet. Start with the ones most jobs ask for.', 'missing')] %}
{% set common = groups[kind].common %}{% set rare = groups[kind].rare %}
{% if common or rare %}<div class="card"><h2>{{ title }}</h2><div class="note" style="margin-bottom:4px">{{ help }}</div>
{% for s in common %}{{ skill_row(s) }}{% endfor %}
{% if rare %}<details style="margin-top:10px"><summary>{{ rare|length }} more asked by only one job</summary>
{% for s in rare %}<span class="chip" title="{{ s.learn or 'No free course suggested yet' }}">{{ s.skill }}</span>{% endfor %}</details>{% endif %}
</div>{% endif %}{% endfor %}
{% endif %}
<div class="card"><h2>How to use this</h2><div class="steps">
<div>Pick one or two skills from <b>Start here</b>.</div>
<div>Build something small and real with each, and put it on GitHub.</div>
<div>Tell Claude what you built. Its level in baseline.yaml goes up, and resumes start including it.</div>
</div></div>
<div class="note">Updated {{ generated }}. Counts are exact keyword matches in saved job descriptions.</div>
{% endblock %}""",
}


def create_app(root: Path = ROOT) -> Flask:
    app = Flask(__name__)
    app.jinja_loader = DictLoader(TEMPLATES)
    tracker = Tracker(root)
    if not tracker.setting("ui_secret"):
        tracker.set_setting("ui_secret", secrets.token_hex(24))
    app.secret_key = tracker.setting("ui_secret")
    task: dict = {"name": "", "proc": None, "lines": []}

    def profile() -> Profile:
        return Profile.load(profile_path(root))

    def rows() -> list[dict[str, str]]:
        return tracker.rows()

    def running() -> bool:
        return task["proc"] is not None and task["proc"].poll() is None

    # Browser actions for one job (apply, take over) run beside the main task: a take-over browser waits
    # up to handoff_wait_minutes for you, and must not block "Run agent" or tailoring in the meantime.
    browsers: dict[str, dict] = {}

    def launch(into: dict, name: str, *args: str) -> None:
        proc = spawn_cli(root, *args)
        into.update(name=name, proc=proc, lines=[])

        def pump() -> None:
            for line in proc.stdout or []:
                into["lines"].append(line.rstrip())
        threading.Thread(target=pump, daemon=True).start()

    def start(name: str, *args: str) -> None:
        if running():
            flash(f"{task['name']} is still running. Wait for it to finish.")
            return
        launch(task, name, *args)

    def open_browser(job_id: str, name: str, *args: str) -> bool:
        current = browsers.get(job_id)
        if current and current["proc"].poll() is None:
            flash(f"{current['name']} is already open or working. Look for its browser window "
                  "(it may be behind this one).")
            return False
        browsers[job_id] = {}
        launch(browsers[job_id], name, *args)
        return True

    def open_browsers() -> list[dict]:
        """Browser tasks still working, plus finished ones whose result you have not seen yet (shown once)."""
        out = []
        for b in browsers.values():
            done = b["proc"].poll() is not None
            if done and b.get("shown"):
                continue
            if done:
                b["shown"] = True
            out.append({"name": b["name"], "done": done, "tail": "\n".join(b["lines"][-8:])})
        return out

    def busy() -> bool:
        return running() or any(b["proc"].poll() is None for b in browsers.values())

    @app.before_request
    def local_only():
        # The dashboard can submit applications, so refuse requests that another website
        # (or another machine) tries to send to it.
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            abort(403)
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and origin.split("//")[-1] != request.host:
            abort(403)

    @app.context_processor
    def shared():
        return {"waiting": len(tracker.rows("awaiting-human")), "mode": tracker.setting("apply_mode"),
                "notes": [n for n in tracker.notifications() if n["kind"] != "needs-you"][:3],
                "task": {"name": task["name"], "tail": "\n" + "\n".join(task["lines"][-6:])} if running() else None,
                "browsers": open_browsers()}

    @app.get("/task")
    def task_status():
        return jsonify(running=busy(), name=task["name"], tail="\n".join(task["lines"][-6:]))

    @app.post("/run")
    def run_agent():
        start("Agent run", "run")
        return redirect(url_for("dashboard"))

    @app.post("/note/<int:note_id>/dismiss")
    def dismiss(note_id):
        tracker.clear_notifications(note_id=note_id)
        return redirect(request.referrer or url_for("dashboard"))

    @app.get("/")
    def dashboard():
        all_rows = rows()
        counts = Counter(r["status"] for r in all_rows)
        counts = {s: counts.get(s, 0) for s in STATUSES}
        fresh = [r for r in all_rows if r["status"] == "discovered"]
        fresh.sort(key=lambda r: (FIT_ORDER.get(r["fit"], 2), -int(r["keyword_score"] or 0)))
        ready = [r for r in all_rows if r["status"] == "packet-ready"]
        needs = [r for r in all_rows if r["status"] == "needs-description"]
        log = [f"{e['ts'][:16]}  {e['job_id'] + ' ' if e['job_id'] else ''}{e['message']}" for e in tracker.events(15)]
        since = (datetime.now() - timedelta(hours=36)).strftime("%Y-%m-%d %H:%M")
        recent = sorted((r for r in all_rows if r["status"] in ("applied-auto", "completed-manual")
                         and (r["updated"] or "") >= since), key=lambda r: r["updated"], reverse=True)
        return render_template("dashboard.html", active="home", counts=counts, best=fresh[:10], ready=ready,
                               recent=recent, last_auto=_last_scheduled_run(root), needs=needs, log="\n".join(log) or "No activity yet.",
                               today=datetime.now().strftime("%A, %d %B %Y"))

    @app.get("/needs")
    def needs():
        from career.autoapply import host_kind
        from career.jdsummary import summarize
        from career.packet import _saved

        prof = profile()
        waiting = tracker.rows("awaiting-human")
        for r in waiting:
            r["automatable"] = host_kind(r["apply_url"] or r["url"]) in ("supported", "unsupported")
            path = tracker.jd_dir / f"{r['id']}.txt"
            r["glance"] = (summarize(prof, path.read_text(encoding="utf-8"), r["location"], r["role"],
                                     _saved(tracker, r)).one_line if path.exists() else "")
        return render_template("needs.html", active="needs", rows=waiting)

    @app.get("/jobs")
    def jobs():
        tab = request.args.get("tab", "open")
        tab = tab if tab in JOB_TABS else "open"
        q, fit, status = (request.args.get(k, "").strip() for k in ("q", "fit", "status"))
        words = q.lower().split()

        def matches(r: dict[str, str]) -> bool:
            blob = " ".join(r[k] for k in ("id", "role", "company", "location", "status", "salary", "notes",
                                           "source", "track")).lower()
            return all(w in blob for w in words) and (not fit or r["fit"] == fit) and (not status or
                                                                                       r["status"] == status)
        found = [r for r in rows() if matches(r)]
        counts = {key: sum(1 for r in found if r["status"] in statuses) for key, (_, statuses) in JOB_TABS.items()}
        selected = [r for r in found if r["status"] in JOB_TABS[tab][1]]
        if tab == "applied":
            selected.sort(key=lambda r: r["date_applied"] or r["updated"], reverse=True)
        else:
            selected.sort(key=lambda r: (ACTIVE.index(r["status"]) if r["status"] in ACTIVE else 9,
                                         FIT_ORDER.get(r["fit"], 2), -int(r["keyword_score"] or 0)))
        return render_template("jobs.html", active="jobs", rows=selected, statuses=STATUSES, fit=fit, q=q, tab=tab,
                               tab_labels={k: label for k, (label, _) in JOB_TABS.items()}, tab_counts=counts,
                               applied_statuses=JOB_TABS["applied"][1])

    @app.get("/job/<job_id>")
    def job(job_id):
        from career.autoapply import host_kind
        try:
            r = tracker.get(job_id)
        except KeyError:
            abort(404)
        from career import fit as fitmod
        from career.jdsummary import summarize

        prof = profile()
        jd = tracker.jd_text(r["id"]) if (tracker.jd_dir / f"{r['id']}.txt").exists() else ""
        folder = root / r["output_dir"] if r["output_dir"] else None
        files = sorted(f for f in PACKET_FILES if folder and (folder / f).exists())
        kind = host_kind(r["apply_url"] or r["url"])
        saved: dict = {}
        if folder and (folder / "analysis.json").exists():
            try:
                saved = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
            except (ValueError, OSError):
                saved = {}
        revisions = (saved.get("model_output") or {}).get("revisions") or []
        market = saved.get("market_pay") or None
        glance = (summarize(prof, jd, r["location"], r["role"], saved)
                  if jd and r["status"] != "needs-description" else None)
        answers, cover = [], ""
        if r["output_dir"] and jd:
            from career import answers as ans
            from career.forms import answer_rows
            from career.packet import cover_note

            answers = answer_rows(prof, fitmod.check(prof, jd, r["location"], r["role"]), market)
            extra = ans.load_extra(personal_file(ans.ANSWERS_FILE, root))
            answers += [(_question_label(i["question"]), str(i["answer"])) for i in extra.get("answers") or []
                        if i.get("question") and str(i.get("answer") or "").strip()]
            cover = cover_note(tracker, r)
        return render_template("job.html", active="jobs", r=r, jd=jd, glance=glance, market=market,
                               statuses=STATUSES, files=files, revisions=revisions, answers=answers, cover=cover,
                               submitted=SUBMITTED, history=list(reversed(tracker.events(40, job_id=r["id"]))),
                               automatable=kind in ("supported", "unsupported"), link_help=LINK_HELP[kind],
                               today=datetime.now().strftime("%Y-%m-%d"))

    @app.post("/job/<job_id>/open-folder")
    def open_folder(job_id):
        from career.autoapply import open_packet_folder
        flash(open_packet_folder(tracker, job_id))
        return redirect(url_for("job", job_id=job_id) + "#answers")

    @app.post("/job/<job_id>/revise")
    def revise(job_id):
        instruction = request.form.get("instruction", "").strip()
        if instruction:
            start(f"Revise {job_id}", "revise", job_id, instruction)
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/apply-now")
    def apply_now(job_id):
        open_browser(job_id, f"Apply {job_id}", "apply-now", job_id)
        return redirect(url_for("job", job_id=job_id))

    @app.route("/add", methods=["GET", "POST"])
    def add_job():
        from career import pipeline
        # GET with values = the "Save to Career Agent" bookmark: it only pre-fills the form for you to check.
        source = request.form if request.method == "POST" else request.args
        form = {k: source.get(k, "")[:20000] for k in ("url", "company", "role", "location", "text")}
        mode = tracker.setting("apply_mode")
        next_step = {"review": "It opens the application form filled in; you check it and press Submit.",
                     "auto": "It fills and submits the form if every required question is answered; anything "
                             "unclear goes to Needs you.",
                     "off": "It stops with the packet ready (apply mode is off)."}.get(mode, "")
        if request.method == "POST":
            try:
                if not form["url"].strip().startswith(("https://", "http://")):
                    raise ValueError("The job link must start with https://")
                row, is_new = pipeline.add_manual(tracker, profile(), **form)
            except ValueError as exc:
                flash(str(exc))
                return render_template("add.html", active="add", form=form, next_step=next_step)
            if not is_new:
                flash(f"That link is already saved as {row['id']}.")
            if request.form.get("only_save"):
                flash(f"Saved {row['id']}: keyword {row['keyword_score']}%, fit {row['fit']}.")
            else:
                open_browser(row["id"], f"Apply {row['id']}", "apply-now", row["id"])
            return redirect(url_for("job", job_id=row["id"]))
        return render_template("add.html", active="add", form=form, next_step=next_step)

    @app.post("/job/<job_id>/takeover")
    def takeover(job_id):
        if open_browser(job_id, f"Take over {job_id}", "takeover", job_id):
            flash("A browser window is opening on this computer (about 20 seconds) with the form filled in. "
                  "Finish it and press Submit there. The dashboard stays usable meanwhile.")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/auto-apply")
    def auto_apply(job_id):
        open_browser(job_id, f"Apply {job_id}", "auto-apply", job_id)
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/attach")
    def attach(job_id):
        from career import pipeline
        try:
            updated = pipeline.attach_description(tracker, profile(), job_id, request.form.get("text", ""))
            flash(f"Saved. Keyword {updated['keyword_score']}%, fit {updated['fit']}.")
        except (ValueError, KeyError) as exc:
            flash(str(exc))
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/packet")
    def packet(job_id):
        args = ["apply", job_id] + (["--offline"] if request.form.get("offline") else [])
        code, out = run_cli(root, *args)
        flash(out if code == 0 else f"Could not generate the packet:\n{out}")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/applied")
    def applied(job_id):
        date = request.form.get("date") or datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            flash("Use a date like 2026-11-15.")
            return redirect(url_for("job", job_id=job_id))
        tracker.update(job_id, status="completed-manual", date_applied=date)
        flash(f"Marked applied on {date}.")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/status")
    def status(job_id):
        new = request.form.get("status", "")
        note = request.form.get("note", "").strip()
        try:
            tracker.update(job_id, status=new, **({"notes": note} if note else {}))
            flash(f"{job_id}: status set to {new}.")
        except (ValueError, KeyError) as exc:
            flash(str(exc))
        if request.form.get("back") == "needs":
            return redirect(url_for("needs"))
        if request.form.get("back") == "applied":
            return redirect(url_for("jobs", tab="applied"))
        return redirect(url_for("job", job_id=job_id))

    @app.get("/job/<job_id>/file/<name>")
    def packet_file(job_id, name):
        if name not in PACKET_FILES:
            abort(404)
        try:
            r = tracker.get(job_id)
        except KeyError:
            abort(404)
        if not r["output_dir"]:
            abort(404)
        folder = (root / r["output_dir"]).resolve()
        if root.resolve() not in folder.parents or not (folder / name).exists():
            abort(404)
        return send_from_directory(folder, name, mimetype="text/plain; charset=utf-8" if name.endswith(".md") else None)

    @app.post("/discover")
    def discover():
        code, out = run_cli(root, "discover")
        flash(out or "Done.")
        return redirect(url_for("dashboard"))

    @app.post("/ingest")
    def ingest():
        code, out = run_cli(root, "ingest-inbox")
        flash(out or "Done.")
        return redirect(url_for("dashboard"))

    @app.get("/gaps")
    def gaps():
        report = tracker.gap_report(profile())
        skills = ([dict(s, kind="partial") for s in report["partial"]]
                  + [dict(s, kind="missing") for s in report["missing"]])
        # Most-asked first; on a tie, a coursework skill wins because it is quicker to make real.
        picks = sorted(skills, key=lambda s: (-s["count"], s["kind"] != "partial"))[:3]
        rest = [s for s in skills if s not in picks]
        groups = {kind: {"common": [s for s in rest if s["kind"] == kind and s["count"] > 1],
                         "rare": [s for s in rest if s["kind"] == kind and s["count"] == 1]}
                  for kind in ("partial", "missing")}
        return render_template("gaps.html", active="gaps", total=report["total"], picks=picks, groups=groups,
                               generated=report["generated"])

    @app.get("/activity")
    def activity():
        level = request.args.get("level", "")
        return render_template("logs.html", active="log", events=tracker.events(500, level=level), level=level)

    @app.get("/settings")
    def settings():
        helps = {"sources": "Companies and job boards to search. Titles and locations to keep or leave out.",
                 "answers": "Answers only you know (current CTC, relocation, and so on). The agent uses these on "
                            "application forms and never invents one."}
        files = {}
        for name, rel in EDITABLE.items():
            path = personal_file(rel, root)  # shows the example until you save your own copy
            files[name] = {"path": rel, "help": helps[name],
                           "text": path.read_text(encoding="utf-8") if path.exists() else ""}
        return render_template("settings.html", active="settings", fields=SETTING_HELP, values=tracker.settings(),
                               files=files)

    @app.post("/settings")
    def save_settings():
        for key, (_, choices, _help) in SETTING_HELP.items():
            value = request.form.get(key, "").strip()
            if choices and value not in choices:
                continue
            if not choices and not (value.isdigit() and int(value) <= 1000):
                flash(f"{SETTING_HELP[key][0]}: use a whole number.")
                continue
            if key in DEFAULT_SETTINGS:
                tracker.set_setting(key, value)
        tracker.log(f"settings saved (apply mode {tracker.setting('apply_mode')})", level="run")
        flash("Settings saved.")
        return redirect(url_for("settings"))

    @app.post("/settings/file/<name>")
    def save_file(name):
        if name not in EDITABLE:
            abort(404)
        text = request.form.get("text", "").replace("\r\n", "\n")
        try:
            parsed = yaml.safe_load(text) if text.strip() else {}
            if parsed is not None and not isinstance(parsed, dict):
                raise ValueError("the file must be a list of `key: value` settings")
        except (yaml.YAMLError, ValueError) as exc:
            flash(f"Not saved: {EDITABLE[name]} has a formatting problem.\n{exc}")
            return redirect(url_for("settings"))
        (root / EDITABLE[name]).write_text(text, encoding="utf-8")
        flash(f"Saved {EDITABLE[name]}.")
        return redirect(url_for("settings"))

    return app
