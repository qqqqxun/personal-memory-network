from app.privacy import classify_text, detect_input_warnings, inherit_level, luhn_ok, redact_secrets


def test_redacts_valid_bank_card():
    text = "卡号 4111111111111111 不要外传"
    redacted, hits = redact_secrets(text)
    assert "[银行卡]" in redacted
    assert any(h["kind"] == "bank" for h in hits)
    assert luhn_ok("4111111111111111")


def test_redacts_api_key():
    text = "密钥 sk-abcdefghijklmnopqrstuvwxyz123456"
    result = classify_text(text)
    assert result.level == "confidential"
    assert "[密钥]" in result.redacted


def test_sensitive_topic_not_sent_as_public():
    result = classify_text("最近失眠很严重，想改善睡眠质量")
    assert result.level == "sensitive"


def test_technical_hospital_not_forced_sensitive():
    result = classify_text("医院信息系统的接口鉴权怎么做")
    assert result.level in {"public", "normal"}


def test_inherit_never_downgrades():
    assert inherit_level("sensitive", "public") == "sensitive"
    assert inherit_level("public", "normal") == "normal"


def test_input_warnings_for_phone():
    warnings = detect_input_warnings("联系电话 13812345678")
    assert warnings
    assert warnings[0]["severity"] == "confidential"
