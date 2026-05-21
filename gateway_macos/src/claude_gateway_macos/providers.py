import os
from typing import Any, Dict, Tuple

from fastapi import HTTPException, Request


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _mask_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return "<empty>"
    if len(value) <= 40:
        return value
    return value[:28] + "..." + value[-8:]


class ProviderConfig:
    """单个 provider 的配置。"""

    def __init__(self, name: str, env_prefix: str):
        self.name = name
        self.env_prefix = env_prefix

        # 上游 API key
        self.api_key = os.getenv(f"{env_prefix}_API_KEY", "").strip()

        # 模型配置（优先级：provider 维度 > 全局 > 默认值）
        # 三档：primary (opus) / mid (sonnet) / fast（内部兼容档，默认与 mid 保持一致）
        self.model_primary = (
            os.getenv(f"{env_prefix}_MODEL_PRIMARY", "").strip()
            or os.getenv("MODEL_PRIMARY", "").strip()
        )
        self.model_mid = (
            os.getenv(f"{env_prefix}_MODEL_MID", "").strip()
            or os.getenv("MODEL_MID", "").strip()
        )
        self.model_fast = (
            os.getenv(f"{env_prefix}_MODEL_FAST", "").strip()
            or os.getenv("MODEL_FAST", "").strip()
        )

        # 通用配置
        self.default_max_tokens = _int_env("DEFAULT_MAX_TOKENS", 4096)
        self.min_compat_max_tokens = _int_env("MIN_COMPAT_MAX_TOKENS", 16)
        self.passthrough_metadata = os.getenv("GATEWAY_PASSTHROUGH_METADATA", "").strip().lower() in {
            "1", "true", "yes", "on",
        }

        # 别名配置（Excel 发送简洁名，同时兼容带前缀的长名）
        self.alias_opus = os.getenv("ALIAS_OPUS", "opus").strip()
        self.alias_opus_versioned = os.getenv("ALIAS_OPUS_VERSIONED", "claude-opus-4-5").strip()
        self.alias_sonnet = os.getenv("ALIAS_SONNET", "sonnet").strip()
        self.alias_sonnet_versioned = os.getenv("ALIAS_SONNET_VERSIONED", "claude-sonnet-4-5").strip()
        self.alias_haiku = os.getenv("ALIAS_HAIKU", "haiku").strip()
        self.alias_haiku_versioned = os.getenv("ALIAS_HAIKU_VERSIONED", "claude-haiku-4-5").strip()

        # 发现元数据
        self.discovery_max_input_tokens = _int_env("DISCOVERY_MAX_INPUT_TOKENS", 1000000)
        self.discovery_max_tokens = _int_env("DISCOVERY_MAX_TOKENS", 64000)

    @property
    def image_support(self) -> bool:
        return False

    def resolve_image_support(self, route_kind: str = "") -> bool:
        """根据路由类型决定是否支持图片（默认使用 image_support 属性）。"""
        return self.image_support

    def resolve_upstream_key(self, req: Request) -> str:
        """从环境变量或请求 header 中解析上游 API key。"""
        if self.api_key:
            return self.api_key
        incoming = self._extract_incoming_token(req)
        if incoming:
            return incoming
        raise HTTPException(status_code=401, detail="No API key available (env or incoming token)")

    def _extract_incoming_token(self, req: Request) -> str:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        x_api_key = req.headers.get("x-api-key", "").strip()
        if x_api_key:
            return x_api_key
        return req.headers.get("api-key", "").strip()

    def resolve_upstream_url(self, req: Request) -> Tuple[str, str, str]:
        """
        解析上游 URL。
        返回 (upstream_key, upstream_url, route_kind)。
        """
        raise NotImplementedError

    # 前缀匹配顺序：更具体的放前面（claude-opus → claude-sonnet → claude-haiku）
    _MODEL_PREFIX_TIERS = [
        ("claude-opus", "primary"),
        ("claude-sonnet", "mid"),
        ("claude-haiku", "fast"),
    ]

    def route_model(self, model_id: str, route_kind: str = "") -> str:
        """将 Claude 别名映射到上游模型名。

        Opus   → model_primary（pro 级）
        Sonnet → model_mid（标准级）
        Haiku  → 与 Sonnet 同档（不再单独暴露 Haiku）
        """
        value = (model_id or "").strip()
        if not value:
            return self.model_primary

        # model_mid 默认回退到 model_fast（两档 provider 无需设置 model_mid）
        mid = self.model_mid or self.model_fast
        # 对外不再单独暴露 Haiku，兼容传入时映射到 mid。
        fast_compat = mid

        alias_map = {
            # Opus → primary (pro)
            self.alias_opus: self.model_primary,
            self.alias_opus_versioned: self.model_primary,
            # Sonnet → mid (标准)
            self.alias_sonnet: mid,
            self.alias_sonnet_versioned: mid,
            # Haiku → mid（兼容旧前端/旧缓存请求）
            self.alias_haiku: fast_compat,
            self.alias_haiku_versioned: fast_compat,
            # 简写兼容
            "opus": self.model_primary,
            "sonnet": mid,
            "haiku": fast_compat,
            # 直传上游模型名（透传）
            self.model_primary: self.model_primary,
            self.model_mid: self.model_mid,
            self.model_fast: self.model_fast,
        }
        result = alias_map.get(value)
        if result is not None:
            if result != value:
                print(f"[gateway model] mapped {value!r} -> {result!r}")
            return result

        # 精确匹配未命中 → 前缀匹配（兼容 claude-sonnet-4-6、claude-haiku-5 等未来版本）
        lower = value.lower()
        tier_map = {"primary": self.model_primary, "mid": mid, "fast": fast_compat}
        for prefix, tier in self._MODEL_PREFIX_TIERS:
            if lower.startswith(prefix):
                result = tier_map[tier]
                print(f"[gateway model] prefix-matched {value!r} ({prefix}*) -> {result!r}")
                return result

        # 未知模型 → primary（保守回退）
        print(f"[gateway model] unknown model {value!r}, fallback -> {self.model_primary!r}")
        return self.model_primary


# =============================================================================
# DeepSeek Provider
# =============================================================================

class DeepSeekProvider(ProviderConfig):
    """DeepSeek provider 配置。"""

    def __init__(self):
        super().__init__("deepseek", "DEEPSEEK")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic").rstrip("/")
        self.model_primary = self.model_primary or "deepseek-v4-pro"
        self.model_fast = self.model_fast or "deepseek-v4-flash"
        # DeepSeek 两档：opus→pro, sonnet/haiku→flash（model_mid 回退到 model_fast）

    def resolve_upstream_key(self, req: Request) -> str:
        # env key 优先（已信任，无需格式校验）
        if self.api_key:
            return self.api_key
        token = self._extract_incoming_token(req)
        if not token:
            raise HTTPException(status_code=401, detail="No API key available")
        # incoming key 必须为 sk-* 格式
        if not token.startswith("sk-"):
            raise HTTPException(status_code=401, detail="Invalid API key format for DeepSeek, expected sk-*")
        return token

    def resolve_upstream_url(self, req: Request) -> Tuple[str, str, str]:
        upstream_key = self.resolve_upstream_key(req)
        upstream_url = f"{self.base_url}/v1/messages"
        return upstream_key, upstream_url, "deepseek"


# =============================================================================
# Kimi Provider — codingplan / PAYG 双路由
# =============================================================================

class KimiProvider(ProviderConfig):
    """Kimi (Moonshot) provider 配置，支持 codingplan/PAYG 双路由。"""

    def __init__(self):
        super().__init__("kimi", "KIMI")
        self.upstream_api_key = os.getenv("UPSTREAM_API_KEY", "").strip()
        self.base_url_coding = (
            os.getenv("KIMI_CODING_BASE_URL", "").strip()
            or os.getenv("CODINGPLAN_BASE_URL", "https://api.kimi.com/coding/")
        ).rstrip("/")
        self.base_url_payg = (
            os.getenv("KIMI_PAYG_BASE_URL", "").strip()
            or os.getenv("PAYG_BASE_URL", "https://api.moonshot.cn/anthropic")
        ).rstrip("/")
        self.model_primary = self.model_primary or "kimi-k2.6"
        self.model_mid = self.model_mid or "kimi-k2.5"
        # Kimi 对外仅保留 2.6/2.5 两档：fast 与 mid 保持一致。
        self.model_fast = self.model_mid
        self.coding_model = os.getenv("CODINGPLAN_MODEL", "kimi-for-coding").strip() or "kimi-for-coding"

    @property
    def image_support(self) -> bool:
        return True

    def resolve_upstream_key(self, req: Request) -> str:
        if self.api_key:
            return self.api_key
        incoming = self._extract_incoming_token(req)
        if incoming:
            return incoming
        if self.upstream_api_key:
            return self.upstream_api_key
        raise HTTPException(status_code=401, detail="No API key available (env or incoming token)")

    def _classify_key_prefix(self, api_key: str) -> str:
        """根据 key 前缀分类路由。注意：更具体的前缀必须先检查。"""
        value = (api_key or "").strip().lower()
        # 先检查更具体的前缀，再检查通用前缀
        if value.startswith("sk-kimi-"):
            return "sk-kimi-*"
        if value.startswith("sk-"):
            return "sk-*"
        raise HTTPException(status_code=401, detail="Invalid API key format for Kimi, expected sk-*")

    def resolve_upstream_url(self, req: Request) -> Tuple[str, str, str]:
        upstream_key = self.resolve_upstream_key(req)
        key_class = self._classify_key_prefix(upstream_key)
        if key_class == "sk-kimi-*":
            return upstream_key, f"{self.base_url_coding}/v1/messages", "kimi:codingplan"
        return upstream_key, f"{self.base_url_payg}/v1/messages", "kimi:payg"

    def route_model(self, model_id: str, route_kind: str = "") -> str:
        if route_kind in ("codingplan", "kimi:codingplan"):
            return self.coding_model
        return super().route_model(model_id)


# =============================================================================
# MiMo Provider — PAYG / Token Plan 多区域路由
# =============================================================================

class MiMoProvider(ProviderConfig):
    """MiMo provider 配置，支持 PAYG 和 Token Plan 多区域路由。"""

    def __init__(self):
        super().__init__("mimo", "MIMO")
        self.base_url_payg = os.getenv("MIMO_PAYG_BASE_URL", "https://api.xiaomimimo.com/anthropic").rstrip("/")
        self.tp_region_default = os.getenv("MIMO_TP_REGION", "cn").strip().lower()
        self.tp_base_urls = {
            "cn": os.getenv("MIMO_TP_BASE_URL_CN", "https://token-plan-cn.xiaomimimo.com/anthropic").rstrip("/"),
            "sgp": os.getenv("MIMO_TP_BASE_URL_SGP", "https://token-plan-sgp.xiaomimimo.com/anthropic").rstrip("/"),
            "ams": os.getenv("MIMO_TP_BASE_URL_AMS", "https://token-plan-ams.xiaomimimo.com/anthropic").rstrip("/"),
        }
        self.model_primary = self.model_primary or "mimo-v2.5-pro"
        self.model_mid = self.model_mid or "mimo-v2.5"
        # MiMo 对外仅保留 v2.5-pro / v2.5 两档：fast 与 mid 保持一致。
        self.model_fast = self.model_mid

        if self.tp_region_default not in self.tp_base_urls:
            print(f"[gateway startup] invalid MIMO_TP_REGION={self.tp_region_default!r}; fallback=cn")
            self.tp_region_default = "cn"

    def resolve_upstream_key(self, req: Request) -> str:
        token = self.api_key or self._extract_incoming_token(req)
        if not token:
            raise HTTPException(status_code=401, detail="No API key available (env or incoming token)")
        if token.startswith("sk-") or token.startswith("tp-"):
            return token
        raise HTTPException(status_code=401, detail="Invalid API key prefix for MiMo, expected sk- or tp-")

    def _resolve_tp_region(self, req: Request) -> str:
        override_region = req.headers.get("x-mimo-tp-region", "").strip().lower()
        if not override_region:
            return self.tp_region_default
        if override_region not in self.tp_base_urls:
            raise HTTPException(
                status_code=400,
                detail="Invalid x-mimo-tp-region, expected one of: cn, sgp, ams",
            )
        return override_region

    def resolve_upstream_url(self, req: Request) -> Tuple[str, str, str]:
        upstream_key = self.resolve_upstream_key(req)
        region = self._resolve_tp_region(req)
        if upstream_key.startswith("sk-"):
            return upstream_key, f"{self.base_url_payg}/v1/messages", "mimo:payg"
        if upstream_key.startswith("tp-"):
            base_url = self.tp_base_urls[region]
            return upstream_key, f"{base_url}/v1/messages", f"mimo:token-plan:{region}"
        raise HTTPException(status_code=401, detail="Invalid API key prefix, expected sk- or tp-")


# =============================================================================
# MiniMax Provider — PAYG / Coding Plan 双计费（同 Anthropic 协议）
# =============================================================================

class MiniMaxProvider(ProviderConfig):
    """MiniMax provider 配置，支持 PAYG 与 Coding Plan（Token Plan）双计费。"""

    def __init__(self):
        super().__init__("minimax", "MINIMAX")
        self.base_urls = {
            "cn": os.getenv("MINIMAX_BASE_URL_CN", "https://api.minimaxi.com/anthropic").rstrip("/"),
            "global": os.getenv("MINIMAX_BASE_URL_GLOBAL", "https://api.minimax.io/anthropic").rstrip("/"),
        }
        self.region_default = os.getenv("MINIMAX_REGION", "cn").strip().lower() or "cn"

        self.model_primary = self.model_primary or "MiniMax-M2.7"
        self.model_mid = self.model_mid or "MiniMax-M2.5"
        self.model_fast = self.model_fast or "MiniMax-M2.5-highspeed"

        if self.region_default not in self.base_urls:
            print(f"[gateway startup] invalid MINIMAX_REGION={self.region_default!r}; fallback=cn")
            self.region_default = "cn"

    def resolve_upstream_key(self, req: Request) -> str:
        token = self.api_key or self._extract_incoming_token(req)
        if not token:
            raise HTTPException(status_code=401, detail="No API key available (env or incoming token)")

        # MiniMax 官方双计费 key：
        #   sk-api-* -> PAYG
        #   sk-cp-*  -> Coding Plan / Token Plan
        if token.startswith("sk-api-") or token.startswith("sk-cp-"):
            return token

        raise HTTPException(status_code=401, detail="Invalid API key prefix for MiniMax, expected sk-api- or sk-cp-")

    def _resolve_region(self, req: Request) -> str:
        override_region = req.headers.get("x-minimax-region", "").strip().lower()
        if not override_region:
            return self.region_default
        if override_region not in self.base_urls:
            raise HTTPException(
                status_code=400,
                detail="Invalid x-minimax-region, expected one of: cn, global",
            )
        return override_region

    def resolve_upstream_url(self, req: Request) -> Tuple[str, str, str]:
        upstream_key = self.resolve_upstream_key(req)
        region = self._resolve_region(req)
        base_url = self.base_urls[region]

        if upstream_key.startswith("sk-api-"):
            return upstream_key, f"{base_url}/v1/messages", f"minimax:payg:{region}"
        if upstream_key.startswith("sk-cp-"):
            return upstream_key, f"{base_url}/v1/messages", f"minimax:codingplan:{region}"

        raise HTTPException(status_code=401, detail="Invalid API key prefix, expected sk-api- or sk-cp-")

    def route_model(self, model_id: str, route_kind: str = "") -> str:
        """MiniMax 三档映射：Opus->primary, Sonnet->mid, Haiku->fast。"""
        value = (model_id or "").strip()
        if not value:
            return self.model_primary

        alias_map = {
            self.alias_opus: self.model_primary,
            self.alias_opus_versioned: self.model_primary,
            self.alias_sonnet: self.model_mid,
            self.alias_sonnet_versioned: self.model_mid,
            self.alias_haiku: self.model_fast,
            self.alias_haiku_versioned: self.model_fast,
            "opus": self.model_primary,
            "sonnet": self.model_mid,
            "haiku": self.model_fast,
            self.model_primary: self.model_primary,
            self.model_mid: self.model_mid,
            self.model_fast: self.model_fast,
        }
        result = alias_map.get(value)
        if result is not None:
            if result != value:
                print(f"[gateway model] mapped {value!r} -> {result!r}")
            return result

        lower = value.lower()
        if lower.startswith("claude-opus"):
            print(f"[gateway model] prefix-matched {value!r} (claude-opus*) -> {self.model_primary!r}")
            return self.model_primary
        if lower.startswith("claude-sonnet"):
            print(f"[gateway model] prefix-matched {value!r} (claude-sonnet*) -> {self.model_mid!r}")
            return self.model_mid
        if lower.startswith("claude-haiku"):
            print(f"[gateway model] prefix-matched {value!r} (claude-haiku*) -> {self.model_fast!r}")
            return self.model_fast

        print(f"[gateway model] unknown model {value!r}, fallback -> {self.model_primary!r}")
        return self.model_primary


# =============================================================================
# Auto Provider — 根据 incoming key 前缀自动路由到对应 provider
# =============================================================================

class AutoProvider(ProviderConfig):
    """
    自动路由 provider：根据请求中的 API key 前缀自动选择上游 provider。

    路由规则：
      dk-*     → DeepSeek
      sk-kimi-* → Kimi codingplan
      sk-mimo-* → MiMo PAYG
      tp-*     → MiMo Token Plan
      sk-*     → MiMo PAYG（默认，因为 MiMo 的 PAYG 最常用）

    优势：无需手动切换 ACTIVE_PROVIDER，一个网关实例同时服务所有 provider。
    限制：无法同时使用 Kimi PAYG 和 MiMo PAYG（都是 sk-* 前缀）。
          如需区分，Kimi 请用 sk-kimi-* 前缀，MiMo 请用 sk-mimo-* 前缀。
    """

    def __init__(self):
        # AutoProvider 不绑定单一 provider，而是持有所有 provider 实例
        self.name = "auto"
        self.env_prefix = ""

        # 加载所有 provider 配置
        self._deepseek = DeepSeekProvider()
        self._kimi = KimiProvider()
        self._mimo = MiMoProvider()
        self._minimax = MiniMaxProvider()

        # 通用配置（从任意 provider 或全局读取）
        self.default_max_tokens = _int_env("DEFAULT_MAX_TOKENS", 4096)
        self.min_compat_max_tokens = _int_env("MIN_COMPAT_MAX_TOKENS", 16)
        self.passthrough_metadata = os.getenv("GATEWAY_PASSTHROUGH_METADATA", "").strip().lower() in {
            "1", "true", "yes", "on",
        }

        # 别名配置（Excel 发送简洁名，同时兼容带前缀的长名）
        self.alias_opus = os.getenv("ALIAS_OPUS", "opus").strip()
        self.alias_opus_versioned = os.getenv("ALIAS_OPUS_VERSIONED", "claude-opus-4-5").strip()
        self.alias_sonnet = os.getenv("ALIAS_SONNET", "sonnet").strip()
        self.alias_sonnet_versioned = os.getenv("ALIAS_SONNET_VERSIONED", "claude-sonnet-4-5").strip()
        self.alias_haiku = os.getenv("ALIAS_HAIKU", "haiku").strip()
        self.alias_haiku_versioned = os.getenv("ALIAS_HAIKU_VERSIONED", "claude-haiku-4-5").strip()

        self.discovery_max_input_tokens = _int_env("DISCOVERY_MAX_INPUT_TOKENS", 1000000)
        self.discovery_max_tokens = _int_env("DISCOVERY_MAX_TOKENS", 64000)

        # 默认模型（用于未知路由时的回退）
        self.model_primary = self._mimo.model_primary
        self.model_mid = self._mimo.model_mid
        self.model_fast = self._mimo.model_fast

        print(
            "[gateway startup] provider=auto "
            f"deepseek_base={_mask_url(self._deepseek.base_url)} "
            f"kimi_coding={_mask_url(self._kimi.base_url_coding)} "
            f"kimi_payg={_mask_url(self._kimi.base_url_payg)} "
            f"mimo_payg={_mask_url(self._mimo.base_url_payg)}"
        )

    @property
    def image_support(self) -> bool:
        # 取决于实际路由到的 provider，这里保守返回 True（Kimi 支持）
        return True

    def _extract_incoming_token(self, req: Request) -> str:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        x_api_key = req.headers.get("x-api-key", "").strip()
        if x_api_key:
            return x_api_key
        return req.headers.get("api-key", "").strip()

    def _detect_provider_and_key(self, req: Request) -> Tuple[ProviderConfig, str]:
        """根据 incoming key 前缀检测应该使用哪个 provider。"""
        # 优先检查各 provider 的 env key
        for provider in [self._deepseek, self._kimi, self._mimo, self._minimax]:
            if provider.api_key:
                print(
                    f"[gateway auto] env key found for {provider.name}, "
                    "locking all traffic to this provider. "
                    "Remove env key to enable per-request key routing."
                )
                return provider, provider.api_key

        # 没有 env key，根据 incoming key 前缀路由
        token = self._extract_incoming_token(req)
        if not token:
            raise HTTPException(status_code=401, detail="No API key available (env or incoming token)")

        lower = token.lower()

        # dk-* → DeepSeek（网关约定前缀，用于区分 provider）
        if lower.startswith("dk-"):
            return self._deepseek, token

        # sk-kimi-* → Kimi codingplan
        if lower.startswith("sk-kimi-"):
            return self._kimi, token

        # tp-* → MiMo Token Plan
        if lower.startswith("tp-"):
            return self._mimo, token

        # sk-mimo-* → MiMo PAYG（明确前缀，无歧义）
        if lower.startswith("sk-mimo-"):
            return self._mimo, token

        # sk-* → 默认 MiMo PAYG（最常见的用法）
        if lower.startswith("sk-"):
            print("[gateway auto] ambiguous sk-* key, defaulting to MiMo PAYG. "
                  "Use sk-kimi-* for Kimi, sk-mimo-* for MiMo, dk-* for DeepSeek.")
            return self._mimo, token

        raise HTTPException(
            status_code=401,
            detail=(
                "Cannot auto-detect provider from key prefix. "
                "Expected: dk-* (DeepSeek), sk-kimi-* (Kimi), sk-mimo-* or tp-* (MiMo). "
                "Or set ACTIVE_PROVIDER to a specific provider and configure its API key."
            ),
        )

    def resolve_upstream_key(self, req: Request) -> str:
        raise NotImplementedError("AutoProvider uses resolve_upstream_url directly")

    def resolve_upstream_url(self, req: Request) -> Tuple[str, str, str]:
        detected_provider, token = self._detect_provider_and_key(req)
        print(f"[gateway auto] detected provider={detected_provider.name}")

        # 委托给具体 provider 的完整决策逻辑，避免手写分支丢失细节
        if detected_provider is self._deepseek:
            return token, f"{self._deepseek.base_url}/v1/messages", "deepseek"

        if detected_provider is self._kimi:
            # 复用 KimiProvider 的路由逻辑（含 codingplan/PAYG 细分）
            key_class = self._kimi._classify_key_prefix(token)
            if key_class == "sk-kimi-*":
                return token, f"{self._kimi.base_url_coding}/v1/messages", "kimi:codingplan"
            return token, f"{self._kimi.base_url_payg}/v1/messages", "kimi:payg"

        if detected_provider is self._mimo:
            # 复用 MiMoProvider 的完整路由逻辑（含区域覆写头）
            region = self._mimo._resolve_tp_region(req)
            if token.lower().startswith("tp-"):
                base_url = self._mimo.tp_base_urls[region]
                return token, f"{base_url}/v1/messages", f"mimo:token-plan:{region}"
            return token, f"{self._mimo.base_url_payg}/v1/messages", "mimo:payg"

        raise HTTPException(status_code=500, detail="Internal: no provider matched")

    def resolve_image_support(self, route_kind: str) -> bool:
        """根据实际路由决定是否支持图片。"""
        if route_kind.startswith("kimi:"):
            return True
        # DeepSeek 和 MiMo 默认不支持图片
        return False

    def route_model(self, model_id: str, route_kind: str = "") -> str:
        """根据 route_kind 选择对应 provider 的模型映射。"""
        # kimi:codingplan → Kimi 专用模型
        if route_kind == "kimi:codingplan":
            return self._kimi.coding_model

        # kimi:payg → Kimi 普通模型
        if route_kind == "kimi:payg":
            return self._kimi.route_model(model_id)

        # deepseek 路由
        if route_kind == "deepseek":
            return self._deepseek.route_model(model_id, route_kind)

        # minimax:* -> MiniMax
        if route_kind.startswith("minimax:"):
            return self._minimax.route_model(model_id, route_kind)

        # mimo:token-plan / mimo:payg → MiMo
        if route_kind.startswith("mimo:"):
            return self._mimo.route_model(model_id, route_kind)

        # fallback
        return self._mimo.route_model(model_id, route_kind)


# =============================================================================
# Provider 注册表和加载逻辑
# =============================================================================

PROVIDER_REGISTRY: Dict[str, type] = {
    "deepseek": DeepSeekProvider,
    "kimi": KimiProvider,
    "mimo": MiMoProvider,
    "minimax": MiniMaxProvider,
    "auto": AutoProvider,
}


def load_provider() -> ProviderConfig:
    """
    根据 ACTIVE_PROVIDER 环境变量加载对应的 provider 配置。

    可选值：
      - deepseek: DeepSeek（需要 DEEPSEEK_API_KEY）
      - kimi: Kimi/Moonshot（需要 KIMI_API_KEY）
      - mimo: MiMo（需要 MIMO_API_KEY）
      - minimax: MiniMax（需要 MINIMAX_API_KEY）
      - auto: 自动模式，根据 incoming key 前缀路由（无需设置单一 API key）
    """
    active = os.getenv("ACTIVE_PROVIDER", "").strip().lower()
    if not active:
        raise RuntimeError(
            "ACTIVE_PROVIDER environment variable is not set. "
            f"Choose one of: {', '.join(PROVIDER_REGISTRY.keys())}"
        )
    cls = PROVIDER_REGISTRY.get(active)
    if cls is None:
        raise RuntimeError(
            f"Unknown ACTIVE_PROVIDER={active!r}. "
            f"Choose one of: {', '.join(PROVIDER_REGISTRY.keys())}"
        )
    provider = cls()
    if active != "auto":
        print(
            f"[gateway startup] provider={provider.name} "
            f"model_primary={provider.model_primary} model_mid={provider.model_mid} model_fast={provider.model_fast}"
        )
    return provider
