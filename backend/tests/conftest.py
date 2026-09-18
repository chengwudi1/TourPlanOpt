"""测试期把出站配置钉成惰性值：开发者 .env 里那把真密钥不能决定任何一条断言的成败。

两重理由，缺一不可：

1. **可复现**。``llm_ready()`` 一旦因为本机配了密钥而为真，「规则解析」那一整批用例就会
   悄悄改走模型那条路，红在谁也想不到的地方 —— 同一份代码在配了模型的机器上失败、在没配
   的机器上通过，这种测试等于没有。
2. **钱包与密钥**。某个用例忘了 fake 掉 HTTP 层时，它发出去的是真请求：烧真配额，还把真
   密钥交到云上。这里默认清空，用例想用模型就自己 ``monkeypatch.setattr`` 一个假端点。

个别用例确实需要这些值（比如断言请求体里的模型名），它自己 setattr 即可 —— autouse 先跑，
用例后跑，覆盖关系是直的。
"""

from __future__ import annotations

import pytest

from app.config import settings

# 回到 config.py 里的默认值：断言应当只对默认值负责，不对谁的 .env 负责。
_INERT = {
    "llm_api_key": "",
    "llm_model": "glm-4-flash",
    "asr_api_key": "",
    "asr_base_url": "",
    "asr_model": "whisper-1",
    "asr_language": "",
    # 高德的 key 走 query 参数，忘了 fake 就是把它打进一次真请求里。
    "amap_web_key": "",
}


@pytest.fixture(autouse=True)
def _inert_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _INERT.items():
        monkeypatch.setattr(settings, name, value)
