from dictation4me.markdown import render


def test_raw_html_and_script_never_emitted():
    result = render('<script>alert("x")</script>\n# Title\n- item\n**Bold**')
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    assert "<h1>Title</h1>" in result
    assert "<ul><li>item</li></ul>" in result
    assert "<strong>Bold</strong>" in result


def test_code_fence_escapes_html():
    result = render('```\n<img src="https://example.com/x" />\n```')
    assert "<img" not in result
    assert "&lt;img" in result
