from bannaha.tools.documents import DocumentStore, html_to_text
from bannaha.tools.sandbox import ProcessBackend, resolve_backend


def test_process_sandbox_runs_and_reports_errors():
    be = ProcessBackend()
    ok = be.run_python("print(6*7)")
    assert ok["ok"] and ok["stdout"].strip() == "42"
    bad = be.run_python("import nope_not_a_module")
    assert not bad["ok"] and "ModuleNotFoundError" in bad["error"]
    slow = be.run_python("import time; time.sleep(5)", timeout_s=0.5)
    assert slow["timeout"] and not slow["ok"]


def test_resolve_backend():
    assert resolve_backend("process").name == "process"
    assert resolve_backend("docker").name == "docker"


def test_html_to_text_and_document_search():
    title, text = html_to_text("<html><head><title>T</title><script>x()</script></head>"
                               "<body><p>Saudi exports rose to 2.9 million bpd in September.</p>"
                               "<p>Unrelated paragraph about weather patterns in the region.</p></body></html>")
    assert title == "T" and "x()" not in text
    store = DocumentStore()
    store.add("https://a.org", title, text + "\n\n" + "Exports in August averaged roughly 700,000 bpd overall.")
    hits = store.search("September exports bpd")
    assert hits and "2.9 million" in hits[0]["text"]


def test_web_tools_are_left_out_when_web_is_off(make_session):
    s = make_session("vote", "mock@local:42*2", web=False, python=True)
    names = [t.name for t in s.tools("web_search", "fetch_page", "search_documents", "run_python", "final_answer")]
    assert names == ["run_python", "final_answer"]
