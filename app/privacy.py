from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import LEVEL_RANK, LEVELS

PLACEHOLDERS = {
    "id_card": "[身份证]",
    "phone": "[手机号]",
    "bank": "[银行卡]",
    "email": "[邮箱]",
    "secret": "[密钥]",
    "address": "[精确住址]",
}

ID_CARD_RE = re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
BANK_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){16,19}(?!\d)")
SECRET_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{20,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|"
    r"(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{8,})"
)
ADDRESS_RE = re.compile(r"[\u4e00-\u9fff]{2,}(?:省|市|区|县|镇|乡|街道|路|街|巷|号院?){1,4}\d+号")

SENSITIVE_TOPIC_RE = re.compile(
    r"(失眠|睡眠质量|血压|血糖|处方|抑郁症|焦虑症|体检报告|"
    r"工资单|银行卡余额|贷款合同|信用卡账单|理财账户|"
    r"妻子|老公|孩子学费|父母生病|家暴|"
    r"同事纠纷|被开除|离婚协议)"
)

TECHNICAL_OVERRIDE_RE = re.compile(r"(医院信息系统|医疗影像|电子病历接口|工资管理系统|财务软件)")


def luhn_ok(number: str) -> bool:
    digits = [int(ch) for ch in re.sub(r"\D", "", number)]
    if not (16 <= len(digits) <= 19):
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def id_card_ok(value: str) -> bool:
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    mapping = "10X98765432"
    body = value[:17]
    if not body.isdigit():
        return False
    total = sum(int(ch) * w for ch, w in zip(body, weights))
    return mapping[total % 11] == value[-1].upper()


@dataclass
class PrivacyResult:
    level: str
    redacted: str
    hits: list[dict] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    @property
    def confidential(self) -> bool:
        return self.level == "confidential"


def _add_hit(hits: list[dict], kind: str, value: str) -> None:
    hits.append({"kind": kind, "placeholder": PLACEHOLDERS[kind], "sample": value[:4] + "…" if len(value) > 4 else "…"})


def redact_secrets(text: str) -> tuple[str, list[dict]]:
    hits: list[dict] = []
    work = text or ""

    def sub_id(match: re.Match[str]) -> str:
        value = match.group(0)
        if id_card_ok(value):
            _add_hit(hits, "id_card", value)
            return PLACEHOLDERS["id_card"]
        return value

    work = ID_CARD_RE.sub(sub_id, work)

    def sub_phone(match: re.Match[str]) -> str:
        _add_hit(hits, "phone", match.group(0))
        return PLACEHOLDERS["phone"]

    work = PHONE_RE.sub(sub_phone, work)
    work = EMAIL_RE.sub(lambda m: (_add_hit(hits, "email", m.group(0)) or PLACEHOLDERS["email"]), work)
    work = SECRET_RE.sub(lambda m: (_add_hit(hits, "secret", m.group(0)) or PLACEHOLDERS["secret"]), work)
    work = ADDRESS_RE.sub(lambda m: (_add_hit(hits, "address", m.group(0)) or PLACEHOLDERS["address"]), work)

    def sub_bank(match: re.Match[str]) -> str:
        raw = match.group(0)
        if luhn_ok(raw):
            _add_hit(hits, "bank", raw)
            return PLACEHOLDERS["bank"]
        return raw

    work = BANK_RE.sub(sub_bank, work)
    return work, hits


def classify_text(text: str, manual_level: str | None = None) -> PrivacyResult:
    redacted, hits = redact_secrets(text)
    topics = []
    if SENSITIVE_TOPIC_RE.search(redacted) and not TECHNICAL_OVERRIDE_RE.search(redacted):
        topics = list({item.group(0) for item in SENSITIVE_TOPIC_RE.finditer(redacted)})

    if hits:
        level = "confidential"
    elif topics:
        level = "sensitive"
    else:
        level = "normal" if _looks_personal(redacted) else "public"

    if manual_level:
        if LEVEL_RANK[manual_level] > LEVEL_RANK[level]:
            level = manual_level
        # System never auto-downgrades a manual level.
        if LEVEL_RANK[manual_level] >= LEVEL_RANK[level]:
            level = max(level, manual_level, key=lambda item: LEVEL_RANK[item])

    if hits and level != "confidential":
        level = "confidential"
    return PrivacyResult(level=level, redacted=redacted, hits=hits, topics=topics)


def _looks_personal(text: str) -> bool:
    markers = ("我的项目", "我在做", "我准备", "进度", "偏好", "我习惯", "我倾向")
    return any(marker in (text or "") for marker in markers)


def inherit_level(parent: str, child: str) -> str:
    return parent if LEVEL_RANK[parent] >= LEVEL_RANK[child] else child


def can_auto_set_level(old: str, new: str) -> bool:
    return LEVEL_RANK[new] >= LEVEL_RANK[old]


def normalize_level(level: str) -> str:
    if level not in LEVELS:
        raise ValueError(f"unknown sensitivity: {level}")
    return level


def detect_input_warnings(text: str) -> list[dict]:
    result = classify_text(text)
    warnings = []
    if result.hits:
        warnings.append(
            {
                "severity": "confidential",
                "message": "检测到证件号、密钥或精确住址等机密信息，请不要发送到网页版。",
                "hits": result.hits,
            }
        )
    if result.topics:
        warnings.append(
            {
                "severity": "sensitive",
                "message": "内容可能涉及健康、财务、家庭或人际关系。",
                "topics": result.topics,
            }
        )
    return warnings
