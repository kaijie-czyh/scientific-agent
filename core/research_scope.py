"""首版产品的开放范围；不删除旧领域画像，也不迁移历史项目。"""
from __future__ import annotations

DEFAULT_DOMAIN = "computer_science"
PUBLIC_DOMAINS = {
    "computer_science": "软件工程与计算机系统",
    "ai": "AI 与机器学习",
    "security": "计算机安全与隐私",
}
DOMAIN_ALIASES = {
    "cs": "computer_science", "computer science": "computer_science",
    "计算机": "computer_science", "计算机科学": "computer_science",
    "软件工程": "computer_science", "人工智能": "ai",
    "machine learning": "ai", "网络安全": "security",
    "cybersecurity": "security",
}
SCOPE_NOTICE = (
    "当前版本聚焦计算机研究：AI 与机器学习、软件工程与计算机系统、计算机安全与隐私。"
    "系统辅助检索、讨论方法、规划任务和整理用户提交的结果，不代跑实验，不保证创新成立。"
    "暂不支持医学、临床、药物、湿实验及实物设备操作指导，"
    "也不提供真人访谈或人体实验的完整指导与伦理审定。"
    "安全研究仅面向公开数据、模拟环境或明确授权的测试。"
)


def normalize_public_domain(domain: str | None) -> str:
    """空值兼容旧客户端，限定为计算机范围；不修改已保存项目。"""
    value = str(domain or "").strip().lower()
    value = DOMAIN_ALIASES.get(value, value) or DEFAULT_DOMAIN
    if value not in PUBLIC_DOMAINS:
        raise ValueError(
            "当前版本仅开放计算机相关研究，请选择 AI 与机器学习、软件工程与计算机系统、"
            "计算机安全与隐私。原领域的历史项目与材料保留，可继续查看、记录和导出；"
            "暂不启动该领域的新推荐或自动科研指导。"
        )
    return value


def is_public_domain(domain: str | None) -> bool:
    try:
        normalize_public_domain(domain)
        return True
    except ValueError:
        return False
