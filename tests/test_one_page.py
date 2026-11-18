"""One-page fitting: what is removed, in which order, and that PDF and Word stay one page and match."""

import copy

import pytest
from docx import Document
from pypdf import PdfReader

from career import packet, render
from career.profile import ROOT, Profile
from career.render import MAX_SIZE, OnePageError, fit_one_page, removal_order, write_resume
from career.resume import base_content, extra_items, layout
from career.tracker import Tracker

JD = (ROOT / "tests" / "sample_jd_iam.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile():
    return Profile.load()


def cert(name, tracks=(), priority=None):
    return {"name": name, "kind": "certification", "tracks": list(tracks), "priority": priority}


def achievement(name, priority=None):
    return {"name": name, "kind": "achievement", "tracks": [], "priority": priority}


def names(items):
    return [i["name"] for i in items]


def padded(profile, track, extra_achievements):
    """A real base resume plus enough extra achievements to overflow the page at full size."""
    doc = layout(profile, base_content(profile, track))
    doc["certifications"] = doc["certifications"] + [
        achievement(f"Test achievement number {i}, a long enough line", priority=1) for i in range(extra_achievements)]
    return doc


# ---- removal order ---------------------------------------------------------------------------
def test_removal_order_off_track_certs_then_achievements_by_priority():
    doc = {"track": "software", "certifications": [
        cert("Sec A", ["security"]), cert("Everywhere"), cert("Soft", ["software"]),
        cert("Sec B", ["security"], priority=1), cert("Both", ["security", "software"]),
        achievement("Hackathon", priority=5), achievement("Badminton singles", priority=1),
        achievement("No priority"), achievement("Badminton doubles", priority=1)]}
    assert names(removal_order(doc)) == [
        "Sec B", "Sec A",                           # off-track certs, priority first, then bottom-up
        "Badminton doubles", "Badminton singles",   # lowest priority first, ties bottom-up
        "Hackathon", "No priority"]                 # entries without a priority go last
    # certifications with no tracks, or tagged with this track, are never candidates
    assert {"Everywhere", "Soft", "Both"}.isdisjoint(names(removal_order(doc)))


def test_profile_entries_accept_strings_and_tags(profile):
    items = {i["name"]: i for i in extra_items(profile)}
    assert items["Qualys Certified Specialist, VMDR"]["tracks"] == ["security"]
    assert items["College Hackathon, Track Winner"]["kind"] == "achievement"
    plain = copy.deepcopy(profile)
    plain.data["certifications"] = ["Plain string cert"]
    assert extra_items(plain)[0] == cert("Plain string cert")


# ---- fitting --------------------------------------------------------------------------------------
@pytest.mark.parametrize("track", ["security", "software"])
def test_overflow_removes_items_one_at_a_time_in_order_before_shrinking(profile, tmp_path, track):
    doc = padded(profile, track, 30)
    assert render._pages(render._build_pdf(doc, MAX_SIZE)) > 1  # the test really overflows
    fitted = write_resume(doc, tmp_path / "r.pdf", tmp_path / "r.docx")

    order = names(removal_order(doc))
    assert fitted.removed == order[:len(fitted.removed)]
    if fitted.size < MAX_SIZE:  # font shrinks only once every removable item is gone
        assert fitted.removed == order
    # one fewer removal would not have fitted at this size: nothing was dropped needlessly
    if fitted.size == MAX_SIZE and fitted.removed:
        back = dict(fitted.doc, certifications=fitted.doc["certifications"] + [
            i for i in doc["certifications"] if i["name"] == fitted.removed[-1]])
        assert render._pages(render._build_pdf(back, MAX_SIZE)) > 1

    kept = names(fitted.doc["certifications"])
    on_track = [c["name"] for c in doc["certifications"]
                if c["kind"] == "certification" and (not c["tracks"] or track in c["tracks"])]
    assert set(on_track) <= set(kept)
    assert doc["certifications"] != fitted.doc["certifications"]  # input left unchanged
    assert len(doc["certifications"]) == len(layout(profile, base_content(profile, track))["certifications"]) + 30

    reader = PdfReader(tmp_path / "r.pdf")
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for heading in ["Experience", "Projects", "Skills", "Education", "Certifications & Achievements"]:
        assert heading in text
    assert "Example Institute of Technology" in text

    word = Document(tmp_path / "r.docx")
    paragraphs = [p.text for p in word.paragraphs]
    assert paragraphs[-len(kept):] == kept                       # same items as the PDF
    assert word.styles["Normal"].font.size.pt == pytest.approx(fitted.size, abs=0.5)
    for heading in ["Education", "Certifications & Achievements"]:
        assert heading in paragraphs


def test_last_certification_is_never_removed(profile):
    doc = layout(profile, base_content(profile, "security"))
    doc["track"] = "neither"
    doc["certifications"] = [cert("Only cert", ["security"])] + [
        achievement(f"Test achievement number {i}, a long enough line", priority=1) for i in range(40)]
    fitted = fit_one_page(doc)
    assert names(fitted.doc["certifications"])[0] == "Only cert"
    assert "Only cert" not in fitted.removed


def test_too_long_raises_and_writes_nothing(profile, tmp_path):
    doc = layout(profile, base_content(profile, "security"))
    for e in doc["experience"]:
        e["bullets"] = e["bullets"] * 6
    with pytest.raises(OnePageError, match="Nothing was saved"):
        write_resume(doc, tmp_path / "r.pdf", tmp_path / "r.docx")
    assert not (tmp_path / "r.pdf").exists() and not (tmp_path / "r.docx").exists()


def test_packet_logs_layout_error_instead_of_two_page_pdf(profile, tmp_path, monkeypatch):
    t = Tracker(tmp_path)
    row = t.add(company="Acme", role="IAM Analyst", url="", location="Bengaluru", text=JD, track="security",
                keyword_score=75, fit="APPLY", source="manual")
    folder = packet.packet_folder(t, row)
    folder.mkdir(parents=True)
    (folder / "resume.pdf").write_bytes(b"stale")

    def too_long(doc):
        raise OnePageError("still 2 pages")
    monkeypatch.setattr(render, "fit_one_page", too_long)
    said = []
    assert packet.build_packet(t, profile, row["id"], offline=True, say=said.append) == packet.LAYOUT_FAILED
    assert not (folder / "resume.pdf").exists() and not (folder / "resume.docx").exists()
    assert "Resume layout error" in (folder / "report.md").read_text(encoding="utf-8")
    assert t.get(row["id"])["status"] == "discovered"
    assert any("still 2 pages" in e["message"] for e in t.events(10, job_id=row["id"]))
    assert any("still 2 pages" in s for s in said)
