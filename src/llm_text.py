"""
llm_text.py — 统一 LLM 调用入口

对应不变量：
    I6.1: LLM 调用失败必须 fail-loud（标的抽取 / 多 agent / 舞弊检测三种调用都适用）
    I8.1: 每次 LLM 调用必须打印 (model, endpoint, prompt_tokens, completion_tokens,
          estimated_cost_usd) 到日志和 Web UI
    I6.3: Prompt token 上限保护

设计要点：
    1. ★ 失败必须抛异常，禁止任何形式的静默回退（v3.5 的核心教训）
    2. DeepSeek V4 thinking 模式必须强制 disabled（endpoint 含 "deepseek" 时）
    3. token 上限保护：超过模型 80% 时拒绝调用
    4. 三种调用类型只是日志区分，不影响调用逻辑（保持简单）
    5. 重试策略：Empty / Timeout / RateLimit 重试 1 次；Config / Auth / HTTP 不重试

自测：
    python -m llm_text       # 配置错误测试不需要 .env
    DEEPSEEK_API_KEY=sk-xxx python -m llm_text   # 含真实调用测试
"""

import json
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

__version__ = "v1.0.1"


# =====================================================================
# 错误类型
# =====================================================================

class LLMError(Exception):
    """LLM 调用相关错误的基类。所有 LLM 错误必须抛出此类的子类，禁止吞掉。"""


class LLMConfigError(LLMError):
    """配置错误（API Key 缺失、provider 名错等）。不重试。"""


class LLMEmptyResponseError(LLMError):
    """LLM 返回空 content（DeepSeek thinking 模式常见）。重试 1 次。"""


class LLMPromptTooLongError(LLMError):
    """Prompt 超过 token 上限 80%。不发送请求，直接终止。"""


class LLMTimeoutError(LLMError):
    """请求超时。重试 1 次。"""


class LLMAuthError(LLMError):
    """401/403 鉴权失败。不重试。"""


class LLMRateLimitError(LLMError):
    """429 限流。重试 1 次（带退避）。"""


class LLMHTTPError(LLMError):
    """其他 HTTP 错误。不重试。"""


# =====================================================================
# 调用类型（仅用于日志区分）
# =====================================================================

class LLMCallType(Enum):
    TICKER_EXTRACTION = "ticker_extraction"   # 早晚报标的抽取
    AGENT_DECISION = "agent_decision"          # 多 agent 决议
    FRAUD_CHECK = "fraud_check"                # 财务舞弊检测
    OTHER = "other"


# =====================================================================
# Provider 配置（2026.6 版本）
# =====================================================================

PROVIDERS = {
    "deepseek": {
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        # 2026.6 起 DeepSeek V4 系列上线：
        #   - deepseek-v4-flash (推荐默认，284B 总参数 / 13B 激活，1M ctx)
        #   - deepseek-v4-pro   (1.6T 总参数 / 49B 激活，复杂任务)
        # 旧名 deepseek-chat / deepseek-reasoner 兼容到 2026-07-24 15:59 UTC 后退役，
        # 退役前自动路由到 v4-flash 的 non-thinking / thinking 模式。
        "default_model": "deepseek-v4-flash",
        "models": [
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat",       # alias, 退役: 2026-07-24
            "deepseek-reasoner",   # alias, 退役: 2026-07-24
        ],
        "api_key_env": "DEEPSEEK_API_KEY",
        "needs_thinking_disabled": True,
        # USD per 1M tokens (input, output)
        "pricing": {
            "deepseek-v4-flash": (0.14, 0.28),
            "deepseek-v4-pro":   (0.435, 0.87),
            "deepseek-chat":     (0.14, 0.28),    # alias 与 v4-flash 同价
            "deepseek-reasoner": (0.14, 0.28),    # alias 与 v4-flash 同价
        },
        "token_limit": 1_000_000,   # V4 系列原生 1M ctx
        "schema": "openai_compat",
    },
    "anthropic": {
        "endpoint": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-sonnet-4-6",
        "models": ["claude-sonnet-4-6", "claude-opus-4-7"],
        "api_key_env": "ANTHROPIC_API_KEY",
        "needs_thinking_disabled": False,
        "pricing": {
            "claude-sonnet-4-6": (3.0, 15.0),
            "claude-opus-4-7": (15.0, 75.0),
        },
        "token_limit": 200000,
        "schema": "anthropic",
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-5.5",
        "models": ["gpt-5.5", "gpt-5.5-pro"],
        "api_key_env": "OPENAI_API_KEY",
        "needs_thinking_disabled": False,
        "pricing": {
            "gpt-5.5": (2.5, 10.0),
            "gpt-5.5-pro": (15.0, 60.0),
        },
        "token_limit": 128000,
        "schema": "openai_compat",
    },
    "qwen": {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen3.6-plus",
        "models": ["qwen3.6-plus"],
        "api_key_env": "QWEN_API_KEY",
        "needs_thinking_disabled": False,
        "pricing": {"qwen3.6-plus": (0.8, 2.0)},
        "token_limit": 128000,
        "schema": "openai_compat",
    },
    "kimi": {
        "endpoint": "https://api.moonshot.cn/v1/chat/completions",
        "default_model": "kimi-k2.6",
        "models": ["kimi-k2.6"],
        "api_key_env": "KIMI_API_KEY",
        "needs_thinking_disabled": False,
        "pricing": {"kimi-k2.6": (1.0, 3.0)},
        "token_limit": 128000,
        "schema": "openai_compat",
    },
    # gemini 走 OpenAI 兼容端点（如有自建网关）或单独适配，v1.1 添加
}


# =====================================================================
# 调用日志
# =====================================================================

@dataclass
class LLMCallLog:
    call_id: str
    call_type: str
    provider: str
    model: str
    endpoint: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    success: bool = False
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    called_at: str = field(default_factory=lambda: datetime.now().isoformat())


# =====================================================================
# 主入口
# =====================================================================

def llm_text(
    call_type: LLMCallType,
    provider: str,
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout_seconds: int = 60,
    log_dir: Optional[Path] = None,
) -> Tuple[str, LLMCallLog]:
    """
    统一 LLM 文本调用入口。

    Returns:
        (text_response, call_log)

    Raises:
        LLMConfigError: provider 不识别或 API Key 缺失
        LLMPromptTooLongError: prompt 超 token 上限 80%
        LLMEmptyResponseError: 返回空（已重试 1 次仍失败）
        LLMTimeoutError: 超时（已重试 1 次仍失败）
        LLMAuthError: 401/403
        LLMRateLimitError: 429（已重试 1 次仍失败）
        LLMHTTPError: 其他 HTTP 错误或网络错误

    ★ 关键：失败一律抛异常，禁止返回空字符串或占位符模板。
    """
    # 1. 配置解析
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise LLMConfigError(
            f"Unknown provider: {provider!r}. Available: {list(PROVIDERS.keys())}"
        )

    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise LLMConfigError(
            f"{cfg['api_key_env']} not found in environment. "
            f"Check your .env file (it must be literally named '.env', "
            f"not '{cfg['api_key_env']}.env')."
        )

    model = model or cfg["default_model"]

    # 2. Token 上限保护（I6.3）
    prompt_tokens_est = _estimate_tokens(system_prompt + user_prompt)
    token_limit = cfg["token_limit"]
    threshold = int(token_limit * 0.8)
    if prompt_tokens_est > threshold:
        raise LLMPromptTooLongError(
            f"Prompt ~{prompt_tokens_est} tokens estimated; "
            f"model {model} limit = {token_limit} (80% threshold = {threshold}). "
            f"Trim prompt before retrying."
        )

    # 3. 构造请求
    body = _build_body(cfg, model, system_prompt, user_prompt, max_tokens, temperature)
    headers = _build_headers(provider, api_key)

    # 4. 调用 + 重试 1 次
    call_id = f"{call_type.value}-{int(time.time() * 1000)}"
    start = time.time()
    last_error: Optional[LLMError] = None

    for attempt in range(2):  # 1 次原始 + 1 次重试
        try:
            response = _http_post(cfg["endpoint"], headers, body, timeout_seconds)
            text, usage = _extract_response(response, cfg["schema"])

            if not text or not text.strip():
                raise LLMEmptyResponseError(
                    f"Provider {provider} returned empty content. "
                    + (
                        "(DeepSeek thinking mode? Verify 'thinking.disabled' is in body.)"
                        if "deepseek" in cfg["endpoint"]
                        else ""
                    )
                )

            # 成功
            duration = time.time() - start
            cost = _estimate_cost(provider, model, usage)
            log = LLMCallLog(
                call_id=call_id,
                call_type=call_type.value,
                provider=provider,
                model=model,
                endpoint=cfg["endpoint"],
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                estimated_cost_usd=cost,
                duration_seconds=duration,
                success=True,
            )
            _persist_log(log, log_dir)
            _print_log(log)
            return text, log

        except (LLMEmptyResponseError, LLMTimeoutError, LLMRateLimitError) as e:
            last_error = e
            if attempt == 0:
                wait = 2 ** attempt
                print(
                    f"  [llm] {type(e).__name__} on attempt {attempt + 1}, "
                    f"retrying in {wait}s..."
                )
                time.sleep(wait)
            continue
        except (LLMConfigError, LLMAuthError, LLMHTTPError, LLMPromptTooLongError):
            # 不可重试，直接传播
            raise

    # 重试也失败 — 写失败日志 + 重新抛出
    duration = time.time() - start
    log = LLMCallLog(
        call_id=call_id,
        call_type=call_type.value,
        provider=provider,
        model=model,
        endpoint=cfg["endpoint"],
        duration_seconds=duration,
        success=False,
        error_type=type(last_error).__name__ if last_error else "Unknown",
        error_message=str(last_error) if last_error else "Unknown",
    )
    _persist_log(log, log_dir)
    _print_log(log)
    assert last_error is not None  # 不可能为 None
    raise last_error


# =====================================================================
# Helper
# =====================================================================

def _estimate_tokens(text: str) -> int:
    """粗估 token 数。中英混合用 chars/2 + words/2 作启发式（保守偏高）。"""
    return max(1, len(text) // 2 + len(text.split()) // 2)


def _build_headers(provider: str, api_key: str) -> dict:
    if provider == "anthropic":
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def _build_body(
    cfg: dict,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    temperature: float,
) -> dict:
    if cfg["schema"] == "anthropic":
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
    else:
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

    # ★ DeepSeek thinking 强制关闭（v3.5 核心修复，对应坑 #10）
    if cfg.get("needs_thinking_disabled"):
        body["thinking"] = {"type": "disabled"}

    return body


def _http_post(endpoint: str, headers: dict, body: dict, timeout: int) -> dict:
    """POST 调用 + 错误分类。"""
    import requests  # 延迟 import，确保 NO_PROXY 已生效

    try:
        resp = requests.post(endpoint, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.Timeout as e:
        raise LLMTimeoutError(f"Request timeout after {timeout}s: {e}") from e
    except requests.exceptions.RequestException as e:
        raise LLMHTTPError(f"Network error: {type(e).__name__}: {e}") from e

    if resp.status_code in (401, 403):
        raise LLMAuthError(f"Auth failed (status {resp.status_code}): {resp.text[:300]}")
    if resp.status_code == 429:
        raise LLMRateLimitError(f"Rate limited: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise LLMHTTPError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        return resp.json()
    except ValueError as e:
        raise LLMHTTPError(
            f"Response is not valid JSON: {resp.text[:300]}"
        ) from e


def _extract_response(response: dict, schema: str) -> Tuple[str, dict]:
    """返回 (text, usage_dict)。"""
    if schema == "anthropic":
        content = response.get("content", [])
        text = ""
        for block in content:
            if block.get("type") == "text":
                text += block.get("text", "")
        usage = response.get("usage", {})
        return text, {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        }

    # OpenAI-compat
    choices = response.get("choices", [])
    if not choices:
        return "", {}
    text = choices[0].get("message", {}).get("content", "") or ""
    return text, response.get("usage", {}) or {}


def _estimate_cost(provider: str, model: str, usage: dict) -> float:
    cfg = PROVIDERS.get(provider, {})
    prices = cfg.get("pricing", {}).get(model)
    if not prices:
        return 0.0
    p_in, p_out = prices  # USD per 1M tokens
    in_tokens = usage.get("prompt_tokens", 0)
    out_tokens = usage.get("completion_tokens", 0)
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000


def _persist_log(log: LLMCallLog, log_dir: Optional[Path]) -> None:
    """写日志到 ~/.ai-hedge-fund/logs/YYYY-MM-DD.log"""
    if log_dir is None:
        log_dir = Path.home() / ".ai-hedge-fund" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{datetime.now():%Y-%m-%d}.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(log), ensure_ascii=False) + "\n")
    except Exception as e:
        # 日志写失败不能阻塞主流程
        print(f"  [llm] WARN: persist log failed: {e}")


def _print_log(log: LLMCallLog) -> None:
    status = "OK" if log.success else f"FAIL ({log.error_type})"
    print(
        f"  [llm:{log.call_type}] {status} "
        f"provider={log.provider} model={log.model} "
        f"tokens={log.prompt_tokens}+{log.completion_tokens} "
        f"cost=${log.estimated_cost_usd:.4f} "
        f"({log.duration_seconds:.2f}s)"
    )


# =====================================================================
# 自测
# =====================================================================

def _self_test() -> None:
    print("=" * 60)
    print("llm_text.py self-test")
    print("=" * 60)

    print("\nTest 1: LLMConfigError on unknown provider")
    try:
        llm_text(
            call_type=LLMCallType.OTHER,
            provider="fake_provider",
            system_prompt="x",
            user_prompt="x",
        )
        raise AssertionError("Expected LLMConfigError")
    except LLMConfigError as e:
        print(f"  PASS: {e}")

    print("\nTest 2: LLMConfigError on missing API key")
    saved = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        llm_text(
            call_type=LLMCallType.OTHER,
            provider="deepseek",
            system_prompt="x",
            user_prompt="x",
        )
        raise AssertionError("Expected LLMConfigError")
    except LLMConfigError as e:
        print(f"  PASS: {str(e)[:80]}...")
    finally:
        if saved:
            os.environ["DEEPSEEK_API_KEY"] = saved

    print("\nTest 3: LLMPromptTooLongError")
    os.environ["DEEPSEEK_API_KEY"] = os.environ.get("DEEPSEEK_API_KEY", "sk-test-dummy")
    huge = "x" * 200_000  # ~100K tokens estimated
    try:
        llm_text(
            call_type=LLMCallType.OTHER,
            provider="deepseek",
            system_prompt=huge,
            user_prompt="x",
        )
        raise AssertionError("Expected LLMPromptTooLongError")
    except LLMPromptTooLongError as e:
        print(f"  PASS: {str(e)[:80]}...")

    print("\nTest 4: Real call (only if DEEPSEEK_API_KEY is a real key)")
    real_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if real_key and real_key != "sk-test-dummy" and len(real_key) > 20:
        try:
            text, log = llm_text(
                call_type=LLMCallType.OTHER,
                provider="deepseek",
                system_prompt="你是中文助手。回答必须用中文。",
                user_prompt="说一句话证明你能正常返回。",
                max_tokens=200,
            )
            print(f"  PASS: got {len(text)} chars")
            print(f"  Response: {text[:120]}...")
            print(f"  Cost: ${log.estimated_cost_usd:.6f}")
        except LLMError as e:
            print(f"  FAIL: {type(e).__name__}: {e}")
    else:
        print(f"  SKIP: no real DEEPSEEK_API_KEY in env")

    print("\n" + "=" * 60)
    print("Self-test done.")


if __name__ == "__main__":
    _self_test()
