"""测试上游 API 连通性和模型映射。

用法: python scripts/test_connectivity.py
"""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

# 确保 src 布局下 claude_gateway 模块可导入
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from claude_gateway.providers import load_provider


def make_anthropic_request(url: str, api_key: str, model: str, timeout: float = 30.0) -> dict:
    """发送一个最小的 Anthropic Messages API 请求。"""
    body = {
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Say 'ok' only."}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
    return {"status": resp.status_code, "body": resp.text[:500]}


def main():
    provider = load_provider()

    # 测试矩阵: (描述, api_key, url, model)
    tests = []

    # DeepSeek: opus → v4-pro, sonnet → v4-flash, haiku → v4-flash
    ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    ds_url = f"{os.getenv('DEEPSEEK_BASE_URL', '').rstrip('/')}/v1/messages"
    if ds_key:
        ds_prov = provider._deepseek if hasattr(provider, "_deepseek") else provider
        tests.append(("DeepSeek opus→pro", ds_key, ds_url, ds_prov.route_model("opus", "deepseek")))
        tests.append(("DeepSeek sonnet→flash", ds_key, ds_url, ds_prov.route_model("sonnet", "deepseek")))
        tests.append(("DeepSeek haiku→flash", ds_key, ds_url, ds_prov.route_model("haiku", "deepseek")))

    # Kimi codingplan: any alias → kimi-for-coding
    kimi_key = os.getenv("KIMI_API_KEY", "")
    kimi_coding_url = f"{os.getenv('KIMI_CODING_BASE_URL', '').rstrip('/')}/v1/messages"
    if kimi_key:
        kimi_prov = provider._kimi if hasattr(provider, "_kimi") else provider
        tests.append(("Kimi coding opus→for-coding", kimi_key, kimi_coding_url, kimi_prov.route_model("opus", "kimi:codingplan")))
        tests.append(("Kimi coding sonnet→for-coding", kimi_key, kimi_coding_url, kimi_prov.route_model("sonnet", "kimi:codingplan")))

    # Kimi PAYG: opus→k2.6, sonnet→k2.5, haiku→k2.5
    kimi_payg_url = f"{os.getenv('KIMI_PAYG_BASE_URL', '').rstrip('/')}/v1/messages"
    if kimi_key:
        tests.append(("Kimi PAYG opus→k2.6", kimi_key, kimi_payg_url, kimi_prov.route_model("opus", "kimi:payg")))
        tests.append(("Kimi PAYG sonnet→k2.5", kimi_key, kimi_payg_url, kimi_prov.route_model("sonnet", "kimi:payg")))
        tests.append(("Kimi PAYG haiku→k2.5", kimi_key, kimi_payg_url, kimi_prov.route_model("haiku", "kimi:payg")))

    # MiMo PAYG: opus→v2.5-pro, sonnet→v2.5, haiku→v2-flash
    mimo_key = os.getenv("MIMO_API_KEY", "")
    mimo_payg_url = f"{os.getenv('MIMO_PAYG_BASE_URL', '').rstrip('/')}/v1/messages"
    if mimo_key:
        mimo_prov = provider._mimo if hasattr(provider, "_mimo") else provider
        tests.append(("MiMo PAYG opus→v2.5-pro", mimo_key, mimo_payg_url, mimo_prov.route_model("opus", "mimo:payg")))
        tests.append(("MiMo PAYG sonnet→v2.5", mimo_key, mimo_payg_url, mimo_prov.route_model("sonnet", "mimo:payg")))
        tests.append(("MiMo PAYG haiku→v2-flash", mimo_key, mimo_payg_url, mimo_prov.route_model("haiku", "mimo:payg")))

    # MiMo TP: opus→v2.5-pro, sonnet→v2.5
    mimo_tp_url = f"{os.getenv('MIMO_TP_BASE_URL_CN', '').rstrip('/')}/v1/messages"
    if mimo_key:
        tests.append(("MiMo TP opus→v2.5-pro", mimo_key, mimo_tp_url, mimo_prov.route_model("opus", "mimo:token-plan:cn")))
        tests.append(("MiMo TP sonnet→v2.5", mimo_key, mimo_tp_url, mimo_prov.route_model("sonnet", "mimo:token-plan:cn")))

    print(f"\n{'='*70}")
    print(f" 上游连通性测试 ({len(tests)} 项)")
    print(f"{'='*70}\n")

    passed = 0
    failed = 0

    for desc, key, url, model in tests:
        short_key = key[:12] + "..."
        print(f"[TEST] {desc}")
        print(f"       url={url}")
        print(f"       key={short_key}")
        print(f"       model={model}")
        try:
            result = make_anthropic_request(url, key, model)
            status = result["status"]
            body_preview = result["body"][:200]
            if 200 <= status < 300:
                print(f"       [PASS] {status} OK")
                passed += 1
            else:
                print(f"       [FAIL] {status}")
                print(f"       body: {body_preview}")
                failed += 1
        except Exception as e:
            print(f"       [FAIL] {type(e).__name__}: {e}")
            failed += 1
        print()

    print(f"{'='*70}")
    print(f" 结果: {passed} passed, {failed} failed / {len(tests)} total")
    print(f"{'='*70}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
