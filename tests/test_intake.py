from dealscout.intake import ParseError, WatchRequest, build_prompt


def test_watch_request_defaults_all_none():
    req = WatchRequest()
    assert req.title is None
    assert req.max_price is None
    assert req.currency is None
    assert req.min_cut is None


def test_watch_request_accepts_partial():
    req = WatchRequest(title="Elden Ring", min_cut=20)
    assert req.title == "Elden Ring"
    assert req.min_cut == 20
    assert req.max_price is None


def test_parse_error_is_runtime_error():
    assert issubclass(ParseError, RuntimeError)


def test_build_prompt_includes_user_text_and_field_rules():
    prompt = build_prompt("盯艾尔登法环 降到RM120")
    assert "盯艾尔登法环 降到RM120" in prompt   # 原句喂给模型
    assert "title" in prompt
    assert "英文" in prompt                      # 要求翻成英文名
    assert "MYR" in prompt                       # 没说币种默认 MYR
    assert "max_price" in prompt
    assert "min_cut" in prompt
