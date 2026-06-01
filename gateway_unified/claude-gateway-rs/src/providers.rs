#![allow(dead_code)]

use crate::env::{env_bool, env_int, env_str};
use axum::http::HeaderMap;
use std::collections::HashMap;

pub trait Provider: Send + Sync {
    fn name(&self) -> &str;
    fn image_support(&self) -> bool {
        false
    }
    fn resolve_image_support(&self, _route_kind: &str) -> bool {
        self.image_support()
    }
    fn resolve_upstream_url(&self, headers: &HeaderMap)
        -> anyhow::Result<(String, String, String)>;
    fn route_model(&self, model_id: &str, route_kind: &str) -> String;

    // For models API
    fn discovery_max_input_tokens(&self) -> i64;
    fn discovery_max_tokens(&self) -> i64;
    fn alias_opus(&self) -> &str;
    fn alias_opus_versioned(&self) -> &str;
    fn alias_sonnet(&self) -> &str;
    fn alias_sonnet_versioned(&self) -> &str;

    fn default_max_tokens(&self) -> i64;
    fn min_compat_max_tokens(&self) -> i64;
    fn passthrough_metadata(&self) -> bool;
}

#[derive(Clone)]
pub struct BaseProviderConfig {
    pub name: String,
    pub env_prefix: String,
    pub api_key: String,
    pub model_primary: String,
    pub model_mid: String,
    pub model_fast: String,
    pub default_max_tokens: i64,
    pub min_compat_max_tokens: i64,
    pub passthrough_metadata: bool,
    pub alias_opus: String,
    pub alias_opus_versioned: String,
    pub alias_sonnet: String,
    pub alias_sonnet_versioned: String,
    pub alias_haiku: String,
    pub alias_haiku_versioned: String,
    pub discovery_max_input_tokens: i64,
    pub discovery_max_tokens: i64,
    pub alias_map: HashMap<String, String>,
}

impl BaseProviderConfig {
    pub fn new(name: &str, env_prefix: &str) -> Self {
        let api_key = env_str(&format!("{}_API_KEY", env_prefix), "");

        let mut model_primary = env_str(&format!("{}_MODEL_PRIMARY", env_prefix), "");
        if model_primary.is_empty() {
            model_primary = env_str("MODEL_PRIMARY", "");
        }

        let mut model_mid = env_str(&format!("{}_MODEL_MID", env_prefix), "");
        if model_mid.is_empty() {
            model_mid = env_str("MODEL_MID", "");
        }

        let mut model_fast = env_str(&format!("{}_MODEL_FAST", env_prefix), "");
        if model_fast.is_empty() {
            model_fast = env_str("MODEL_FAST", "");
        }

        let mid = if !model_mid.is_empty() {
            &model_mid
        } else {
            &model_fast
        };
        let mut alias_map = HashMap::new();
        alias_map.insert(env_str("ALIAS_OPUS", "opus"), model_primary.clone());
        alias_map.insert(
            env_str("ALIAS_OPUS_VERSIONED", "claude-opus-4-5"),
            model_primary.clone(),
        );
        alias_map.insert(env_str("ALIAS_SONNET", "sonnet"), mid.clone());
        alias_map.insert(
            env_str("ALIAS_SONNET_VERSIONED", "claude-sonnet-4-5"),
            mid.clone(),
        );
        alias_map.insert(env_str("ALIAS_HAIKU", "haiku"), model_fast.clone());
        alias_map.insert(
            env_str("ALIAS_HAIKU_VERSIONED", "claude-haiku-4-5"),
            model_fast.clone(),
        );
        alias_map.insert("opus".to_string(), model_primary.clone());
        alias_map.insert("sonnet".to_string(), mid.clone());
        alias_map.insert("haiku".to_string(), model_fast.clone());
        alias_map.insert(model_primary.clone(), model_primary.clone());
        alias_map.insert(model_mid.clone(), model_mid.clone());
        alias_map.insert(model_fast.clone(), model_fast.clone());

        Self {
            name: name.to_string(),
            env_prefix: env_prefix.to_string(),
            api_key,
            model_primary,
            model_mid,
            model_fast,
            default_max_tokens: env_int("DEFAULT_MAX_TOKENS", 4096),
            min_compat_max_tokens: env_int("MIN_COMPAT_MAX_TOKENS", 16),
            passthrough_metadata: env_bool("GATEWAY_PASSTHROUGH_METADATA", false),
            alias_opus: env_str("ALIAS_OPUS", "opus"),
            alias_opus_versioned: env_str("ALIAS_OPUS_VERSIONED", "claude-opus-4-5"),
            alias_sonnet: env_str("ALIAS_SONNET", "sonnet"),
            alias_sonnet_versioned: env_str("ALIAS_SONNET_VERSIONED", "claude-sonnet-4-5"),
            alias_haiku: env_str("ALIAS_HAIKU", "haiku"),
            alias_haiku_versioned: env_str("ALIAS_HAIKU_VERSIONED", "claude-haiku-4-5"),
            discovery_max_input_tokens: env_int("DISCOVERY_MAX_INPUT_TOKENS", 1000000),
            discovery_max_tokens: env_int("DISCOVERY_MAX_TOKENS", 64000),
            alias_map,
        }
    }

    pub fn extract_incoming_token(&self, headers: &HeaderMap) -> Option<String> {
        if let Some(auth) = headers.get("authorization") {
            let auth_str = auth.to_str().unwrap_or("");
            if auth_str.to_lowercase().starts_with("bearer ") {
                return Some(auth_str[7..].trim().to_string());
            }
        }
        if let Some(api_key) = headers.get("x-api-key") {
            let key_str = api_key.to_str().unwrap_or("").trim();
            if !key_str.is_empty() {
                return Some(key_str.to_string());
            }
        }
        if let Some(api_key) = headers.get("api-key") {
            let key_str = api_key.to_str().unwrap_or("").trim();
            if !key_str.is_empty() {
                return Some(key_str.to_string());
            }
        }
        None
    }

    pub fn resolve_upstream_key(&self, headers: &HeaderMap) -> anyhow::Result<String> {
        if !self.api_key.is_empty() {
            return Ok(self.api_key.clone());
        }
        if let Some(token) = self.extract_incoming_token(headers) {
            return Ok(token);
        }
        anyhow::bail!("No API key available (env or incoming token)")
    }

    pub fn route_model(&self, model_id: &str, _route_kind: &str) -> String {
        let value = model_id.trim();
        if value.is_empty() {
            return self.model_primary.clone();
        }

        if let Some(result) = self.alias_map.get(value) {
            return result.clone();
        }

        let mid = if !self.model_mid.is_empty() {
            &self.model_mid
        } else {
            &self.model_fast
        };

        let lower = value.to_lowercase();
        if lower.starts_with("claude-opus") {
            return self.model_primary.clone();
        }
        if lower.starts_with("claude-sonnet") {
            return mid.clone();
        }
        if lower.starts_with("claude-haiku") {
            return self.model_fast.clone();
        }

        self.model_primary.clone()
    }
}

pub struct DeepSeekProvider {
    base: BaseProviderConfig,
    base_url: String,
}

impl DeepSeekProvider {
    pub fn new() -> Self {
        let mut base = BaseProviderConfig::new("deepseek", "DEEPSEEK");
        let base_url = env_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
            .trim_end_matches('/')
            .to_string();
        if base.model_primary.is_empty() {
            base.model_primary = "deepseek-v4-pro".to_string();
        }
        if base.model_fast.is_empty() {
            base.model_fast = "deepseek-v4-flash".to_string();
        }
        Self { base, base_url }
    }
}

impl Provider for DeepSeekProvider {
    fn name(&self) -> &str {
        &self.base.name
    }
    fn discovery_max_input_tokens(&self) -> i64 {
        self.base.discovery_max_input_tokens
    }
    fn discovery_max_tokens(&self) -> i64 {
        self.base.discovery_max_tokens
    }
    fn alias_opus(&self) -> &str {
        &self.base.alias_opus
    }
    fn alias_opus_versioned(&self) -> &str {
        &self.base.alias_opus_versioned
    }
    fn alias_sonnet(&self) -> &str {
        &self.base.alias_sonnet
    }
    fn alias_sonnet_versioned(&self) -> &str {
        &self.base.alias_sonnet_versioned
    }
    fn default_max_tokens(&self) -> i64 {
        self.base.default_max_tokens
    }
    fn min_compat_max_tokens(&self) -> i64 {
        self.base.min_compat_max_tokens
    }
    fn passthrough_metadata(&self) -> bool {
        self.base.passthrough_metadata
    }

    fn resolve_upstream_url(
        &self,
        headers: &HeaderMap,
    ) -> anyhow::Result<(String, String, String)> {
        let upstream_key = if !self.base.api_key.is_empty() {
            self.base.api_key.clone()
        } else if let Some(token) = self.base.extract_incoming_token(headers) {
            if !token.starts_with("sk-") {
                anyhow::bail!("Invalid API key format for DeepSeek, expected sk-*");
            }
            token
        } else {
            anyhow::bail!("No API key available");
        };
        Ok((
            upstream_key,
            format!("{}/v1/messages", self.base_url),
            "deepseek".to_string(),
        ))
    }

    fn route_model(&self, model_id: &str, route_kind: &str) -> String {
        self.base.route_model(model_id, route_kind)
    }
}

pub struct KimiProvider {
    base: BaseProviderConfig,
    upstream_api_key: String,
    base_url_coding: String,
    base_url_payg: String,
    coding_model: String,
}

impl KimiProvider {
    pub fn new() -> Self {
        let mut base = BaseProviderConfig::new("kimi", "KIMI");
        let upstream_api_key = env_str("UPSTREAM_API_KEY", "");
        let mut base_url_coding = env_str("KIMI_CODING_BASE_URL", "");
        if base_url_coding.is_empty() {
            base_url_coding = env_str("CODINGPLAN_BASE_URL", "https://api.kimi.com/coding/");
        }
        base_url_coding = base_url_coding.trim_end_matches('/').to_string();

        let mut base_url_payg = env_str("KIMI_PAYG_BASE_URL", "");
        if base_url_payg.is_empty() {
            base_url_payg = env_str("PAYG_BASE_URL", "https://api.moonshot.cn/anthropic");
        }
        base_url_payg = base_url_payg.trim_end_matches('/').to_string();

        if base.model_primary.is_empty() {
            base.model_primary = "kimi-k2.6".to_string();
        }
        if base.model_mid.is_empty() {
            base.model_mid = "kimi-k2.5".to_string();
        }
        base.model_fast = base.model_mid.clone();

        let mut coding_model = env_str("CODINGPLAN_MODEL", "kimi-for-coding");
        if coding_model.is_empty() {
            coding_model = "kimi-for-coding".to_string();
        }

        Self {
            base,
            upstream_api_key,
            base_url_coding,
            base_url_payg,
            coding_model,
        }
    }

    fn classify_key_prefix(api_key: &str) -> anyhow::Result<&'static str> {
        let lower = api_key.trim().to_lowercase();
        if lower.starts_with("sk-kimi-") {
            Ok("sk-kimi-*")
        } else if lower.starts_with("sk-") {
            Ok("sk-*")
        } else {
            anyhow::bail!("Invalid API key format for Kimi, expected sk-*")
        }
    }
}

impl Provider for KimiProvider {
    fn name(&self) -> &str {
        &self.base.name
    }
    fn discovery_max_input_tokens(&self) -> i64 {
        self.base.discovery_max_input_tokens
    }
    fn discovery_max_tokens(&self) -> i64 {
        self.base.discovery_max_tokens
    }
    fn alias_opus(&self) -> &str {
        &self.base.alias_opus
    }
    fn alias_opus_versioned(&self) -> &str {
        &self.base.alias_opus_versioned
    }
    fn alias_sonnet(&self) -> &str {
        &self.base.alias_sonnet
    }
    fn alias_sonnet_versioned(&self) -> &str {
        &self.base.alias_sonnet_versioned
    }
    fn default_max_tokens(&self) -> i64 {
        self.base.default_max_tokens
    }
    fn min_compat_max_tokens(&self) -> i64 {
        self.base.min_compat_max_tokens
    }
    fn passthrough_metadata(&self) -> bool {
        self.base.passthrough_metadata
    }

    fn image_support(&self) -> bool {
        true
    }

    fn resolve_upstream_url(
        &self,
        headers: &HeaderMap,
    ) -> anyhow::Result<(String, String, String)> {
        let upstream_key = if !self.base.api_key.is_empty() {
            self.base.api_key.clone()
        } else if let Some(token) = self.base.extract_incoming_token(headers) {
            token
        } else if !self.upstream_api_key.is_empty() {
            self.upstream_api_key.clone()
        } else {
            anyhow::bail!("No API key available (env or incoming token)")
        };

        let key_class = Self::classify_key_prefix(&upstream_key)?;
        if key_class == "sk-kimi-*" {
            Ok((
                upstream_key,
                format!("{}/v1/messages", self.base_url_coding),
                "kimi:codingplan".to_string(),
            ))
        } else {
            Ok((
                upstream_key,
                format!("{}/v1/messages", self.base_url_payg),
                "kimi:payg".to_string(),
            ))
        }
    }

    fn route_model(&self, model_id: &str, route_kind: &str) -> String {
        if route_kind == "codingplan" || route_kind == "kimi:codingplan" {
            return self.coding_model.clone();
        }
        self.base.route_model(model_id, route_kind)
    }
}

pub struct MiMoProvider {
    base: BaseProviderConfig,
    base_url_payg: String,
    tp_region_default: String,
    tp_base_urls: HashMap<String, String>,
}

impl MiMoProvider {
    pub fn new() -> Self {
        let mut base = BaseProviderConfig::new("mimo", "MIMO");
        let base_url_payg = env_str("MIMO_PAYG_BASE_URL", "https://api.xiaomimimo.com/anthropic")
            .trim_end_matches('/')
            .to_string();

        let mut tp_region_default = env_str("MIMO_TP_REGION", "cn").to_lowercase();

        let mut tp_base_urls = HashMap::new();
        tp_base_urls.insert(
            "cn".to_string(),
            env_str(
                "MIMO_TP_BASE_URL_CN",
                "https://token-plan-cn.xiaomimimo.com/anthropic",
            )
            .trim_end_matches('/')
            .to_string(),
        );
        tp_base_urls.insert(
            "sgp".to_string(),
            env_str(
                "MIMO_TP_BASE_URL_SGP",
                "https://token-plan-sgp.xiaomimimo.com/anthropic",
            )
            .trim_end_matches('/')
            .to_string(),
        );
        tp_base_urls.insert(
            "ams".to_string(),
            env_str(
                "MIMO_TP_BASE_URL_AMS",
                "https://token-plan-ams.xiaomimimo.com/anthropic",
            )
            .trim_end_matches('/')
            .to_string(),
        );

        if base.model_primary.is_empty() {
            base.model_primary = "mimo-v2.5-pro".to_string();
        }
        if base.model_mid.is_empty() {
            base.model_mid = "mimo-v2.5".to_string();
        }
        base.model_fast = base.model_mid.clone();

        if !tp_base_urls.contains_key(&tp_region_default) {
            tp_region_default = "cn".to_string();
        }

        Self {
            base,
            base_url_payg,
            tp_region_default,
            tp_base_urls,
        }
    }

    fn resolve_tp_region(&self, headers: &HeaderMap) -> anyhow::Result<String> {
        if let Some(region) = headers.get("x-mimo-tp-region") {
            let override_region = region.to_str().unwrap_or("").trim().to_lowercase();
            if !override_region.is_empty() {
                if !self.tp_base_urls.contains_key(&override_region) {
                    anyhow::bail!("Invalid x-mimo-tp-region, expected one of: cn, sgp, ams");
                }
                return Ok(override_region);
            }
        }
        Ok(self.tp_region_default.clone())
    }
}

impl Provider for MiMoProvider {
    fn name(&self) -> &str {
        &self.base.name
    }
    fn discovery_max_input_tokens(&self) -> i64 {
        self.base.discovery_max_input_tokens
    }
    fn discovery_max_tokens(&self) -> i64 {
        self.base.discovery_max_tokens
    }
    fn alias_opus(&self) -> &str {
        &self.base.alias_opus
    }
    fn alias_opus_versioned(&self) -> &str {
        &self.base.alias_opus_versioned
    }
    fn alias_sonnet(&self) -> &str {
        &self.base.alias_sonnet
    }
    fn alias_sonnet_versioned(&self) -> &str {
        &self.base.alias_sonnet_versioned
    }
    fn default_max_tokens(&self) -> i64 {
        self.base.default_max_tokens
    }
    fn min_compat_max_tokens(&self) -> i64 {
        self.base.min_compat_max_tokens
    }
    fn passthrough_metadata(&self) -> bool {
        self.base.passthrough_metadata
    }

    fn resolve_upstream_url(
        &self,
        headers: &HeaderMap,
    ) -> anyhow::Result<(String, String, String)> {
        let token = if !self.base.api_key.is_empty() {
            self.base.api_key.clone()
        } else if let Some(t) = self.base.extract_incoming_token(headers) {
            t
        } else {
            anyhow::bail!("No API key available (env or incoming token)")
        };

        if !token.starts_with("sk-") && !token.starts_with("tp-") {
            anyhow::bail!("Invalid API key prefix for MiMo, expected sk- or tp-");
        }

        let region = self.resolve_tp_region(headers)?;
        if token.starts_with("sk-") {
            Ok((
                token,
                format!("{}/v1/messages", self.base_url_payg),
                "mimo:payg".to_string(),
            ))
        } else {
            let base_url = self
                .tp_base_urls
                .get(&region)
                .ok_or_else(|| anyhow::anyhow!("Unknown TP region: {}", region))?;
            Ok((
                token,
                format!("{}/v1/messages", base_url),
                format!("mimo:token-plan:{}", region),
            ))
        }
    }

    fn route_model(&self, model_id: &str, route_kind: &str) -> String {
        self.base.route_model(model_id, route_kind)
    }
}

pub struct MiniMaxProvider {
    base: BaseProviderConfig,
    base_urls: HashMap<String, String>,
    region_default: String,
}

impl MiniMaxProvider {
    pub fn new() -> Self {
        let mut base = BaseProviderConfig::new("minimax", "MINIMAX");
        let mut base_urls = HashMap::new();
        base_urls.insert(
            "cn".to_string(),
            env_str("MINIMAX_BASE_URL_CN", "https://api.minimaxi.com/anthropic")
                .trim_end_matches('/')
                .to_string(),
        );
        base_urls.insert(
            "global".to_string(),
            env_str(
                "MINIMAX_BASE_URL_GLOBAL",
                "https://api.minimax.io/anthropic",
            )
            .trim_end_matches('/')
            .to_string(),
        );

        let mut region_default = env_str("MINIMAX_REGION", "cn").to_lowercase();
        if region_default.is_empty() {
            region_default = "cn".to_string();
        }

        if base.model_primary.is_empty() {
            base.model_primary = "MiniMax-M2.7".to_string();
        }
        if base.model_mid.is_empty() {
            base.model_mid = "MiniMax-M2.5".to_string();
        }
        if base.model_fast.is_empty() {
            base.model_fast = "MiniMax-M2.5-highspeed".to_string();
        }

        if !base_urls.contains_key(&region_default) {
            region_default = "cn".to_string();
        }

        Self {
            base,
            base_urls,
            region_default,
        }
    }

    fn resolve_region(&self, headers: &HeaderMap) -> anyhow::Result<String> {
        if let Some(region) = headers.get("x-minimax-region") {
            let override_region = region.to_str().unwrap_or("").trim().to_lowercase();
            if !override_region.is_empty() {
                if !self.base_urls.contains_key(&override_region) {
                    anyhow::bail!("Invalid x-minimax-region, expected one of: cn, global");
                }
                return Ok(override_region);
            }
        }
        Ok(self.region_default.clone())
    }
}

impl Provider for MiniMaxProvider {
    fn name(&self) -> &str {
        &self.base.name
    }
    fn discovery_max_input_tokens(&self) -> i64 {
        self.base.discovery_max_input_tokens
    }
    fn discovery_max_tokens(&self) -> i64 {
        self.base.discovery_max_tokens
    }
    fn alias_opus(&self) -> &str {
        &self.base.alias_opus
    }
    fn alias_opus_versioned(&self) -> &str {
        &self.base.alias_opus_versioned
    }
    fn alias_sonnet(&self) -> &str {
        &self.base.alias_sonnet
    }
    fn alias_sonnet_versioned(&self) -> &str {
        &self.base.alias_sonnet_versioned
    }
    fn default_max_tokens(&self) -> i64 {
        self.base.default_max_tokens
    }
    fn min_compat_max_tokens(&self) -> i64 {
        self.base.min_compat_max_tokens
    }
    fn passthrough_metadata(&self) -> bool {
        self.base.passthrough_metadata
    }

    fn resolve_upstream_url(
        &self,
        headers: &HeaderMap,
    ) -> anyhow::Result<(String, String, String)> {
        let token = if !self.base.api_key.is_empty() {
            self.base.api_key.clone()
        } else if let Some(t) = self.base.extract_incoming_token(headers) {
            t
        } else {
            anyhow::bail!("No API key available (env or incoming token)")
        };

        if !token.starts_with("sk-api-") && !token.starts_with("sk-cp-") {
            anyhow::bail!("Invalid API key prefix for MiniMax, expected sk-api- or sk-cp-");
        }

        let region = self.resolve_region(headers)?;
        let base_url = self
            .base_urls
            .get(&region)
            .ok_or_else(|| anyhow::anyhow!("Unknown region: {}", region))?;

        if token.starts_with("sk-api-") {
            Ok((
                token,
                format!("{}/v1/messages", base_url),
                format!("minimax:payg:{}", region),
            ))
        } else {
            Ok((
                token,
                format!("{}/v1/messages", base_url),
                format!("minimax:codingplan:{}", region),
            ))
        }
    }

    fn route_model(&self, model_id: &str, route_kind: &str) -> String {
        self.base.route_model(model_id, route_kind)
    }
}

pub struct AutoProvider {
    base: BaseProviderConfig,
    deepseek: DeepSeekProvider,
    kimi: KimiProvider,
    mimo: MiMoProvider,
    minimax: MiniMaxProvider,
}

impl AutoProvider {
    pub fn new() -> Self {
        let mimo = MiMoProvider::new();
        let mut base = BaseProviderConfig::new("auto", "");

        base.model_primary = mimo.base.model_primary.clone();
        base.model_mid = mimo.base.model_mid.clone();
        base.model_fast = mimo.base.model_fast.clone();

        Self {
            base,
            deepseek: DeepSeekProvider::new(),
            kimi: KimiProvider::new(),
            mimo,
            minimax: MiniMaxProvider::new(),
        }
    }

    fn detect_provider_and_key<'a>(
        &'a self,
        headers: &HeaderMap,
    ) -> anyhow::Result<(&'a dyn Provider, String)> {
        if !self.deepseek.base.api_key.is_empty() {
            return Ok((&self.deepseek, self.deepseek.base.api_key.clone()));
        }
        if !self.kimi.base.api_key.is_empty() {
            return Ok((&self.kimi, self.kimi.base.api_key.clone()));
        }
        if !self.mimo.base.api_key.is_empty() {
            return Ok((&self.mimo, self.mimo.base.api_key.clone()));
        }
        if !self.minimax.base.api_key.is_empty() {
            return Ok((&self.minimax, self.minimax.base.api_key.clone()));
        }

        let token = if let Some(t) = self.base.extract_incoming_token(headers) {
            t
        } else {
            anyhow::bail!("No API key available (env or incoming token)")
        };

        let lower = token.to_lowercase();
        if lower.starts_with("dk-") {
            return Ok((&self.deepseek, token));
        }
        if lower.starts_with("sk-kimi-") {
            return Ok((&self.kimi, token));
        }
        if lower.starts_with("tp-") {
            return Ok((&self.mimo, token));
        }
        if lower.starts_with("sk-mimo-") {
            return Ok((&self.mimo, token));
        }
        if lower.starts_with("sk-api-") || lower.starts_with("sk-cp-") {
            return Ok((&self.minimax, token));
        }
        if lower.starts_with("sk-") {
            return Ok((&self.mimo, token));
        }

        anyhow::bail!("Cannot auto-detect provider from key prefix. Expected: dk-* (DeepSeek), sk-kimi-* (Kimi), sk-mimo-* or tp-* (MiMo).")
    }
}

impl Provider for AutoProvider {
    fn name(&self) -> &str {
        &self.base.name
    }
    fn discovery_max_input_tokens(&self) -> i64 {
        self.base.discovery_max_input_tokens
    }
    fn discovery_max_tokens(&self) -> i64 {
        self.base.discovery_max_tokens
    }
    fn alias_opus(&self) -> &str {
        &self.base.alias_opus
    }
    fn alias_opus_versioned(&self) -> &str {
        &self.base.alias_opus_versioned
    }
    fn alias_sonnet(&self) -> &str {
        &self.base.alias_sonnet
    }
    fn alias_sonnet_versioned(&self) -> &str {
        &self.base.alias_sonnet_versioned
    }
    fn default_max_tokens(&self) -> i64 {
        self.base.default_max_tokens
    }
    fn min_compat_max_tokens(&self) -> i64 {
        self.base.min_compat_max_tokens
    }
    fn passthrough_metadata(&self) -> bool {
        self.base.passthrough_metadata
    }

    fn resolve_image_support(&self, route_kind: &str) -> bool {
        if route_kind.starts_with("kimi:") {
            return true;
        }
        false
    }

    fn resolve_upstream_url(
        &self,
        headers: &HeaderMap,
    ) -> anyhow::Result<(String, String, String)> {
        let (provider, token) = self.detect_provider_and_key(headers)?;

        if provider.name() == "deepseek" {
            return Ok((
                token,
                format!("{}/v1/messages", self.deepseek.base_url),
                "deepseek".to_string(),
            ));
        }
        if provider.name() == "kimi" {
            let key_class = KimiProvider::classify_key_prefix(&token)?;
            if key_class == "sk-kimi-*" {
                return Ok((
                    token,
                    format!("{}/v1/messages", self.kimi.base_url_coding),
                    "kimi:codingplan".to_string(),
                ));
            } else {
                return Ok((
                    token,
                    format!("{}/v1/messages", self.kimi.base_url_payg),
                    "kimi:payg".to_string(),
                ));
            }
        }
        if provider.name() == "mimo" {
            let region = self
                .mimo
                .resolve_tp_region(headers)
                .unwrap_or_else(|_| "cn".to_string());
            if token.to_lowercase().starts_with("tp-") {
                let base_url = self
                    .mimo
                    .tp_base_urls
                    .get(&region)
                    .ok_or_else(|| anyhow::anyhow!("Unknown TP region: {}", region))?;
                return Ok((
                    token,
                    format!("{}/v1/messages", base_url),
                    format!("mimo:token-plan:{}", region),
                ));
            } else {
                return Ok((
                    token,
                    format!("{}/v1/messages", self.mimo.base_url_payg),
                    "mimo:payg".to_string(),
                ));
            }
        }
        if provider.name() == "minimax" {
            let region = self
                .minimax
                .resolve_region(headers)
                .unwrap_or_else(|_| "cn".to_string());
            let base_url = self
                .minimax
                .base_urls
                .get(&region)
                .ok_or_else(|| anyhow::anyhow!("Unknown region: {}", region))?;
            if token.starts_with("sk-api-") {
                return Ok((
                    token,
                    format!("{}/v1/messages", base_url),
                    format!("minimax:payg:{}", region),
                ));
            } else {
                return Ok((
                    token,
                    format!("{}/v1/messages", base_url),
                    format!("minimax:codingplan:{}", region),
                ));
            }
        }

        anyhow::bail!("Internal: no provider matched")
    }

    fn route_model(&self, model_id: &str, route_kind: &str) -> String {
        if route_kind == "kimi:codingplan" {
            return self.kimi.coding_model.clone();
        }
        if route_kind == "kimi:payg" {
            return self.kimi.route_model(model_id, route_kind);
        }
        if route_kind == "deepseek" {
            return self.deepseek.route_model(model_id, route_kind);
        }
        if route_kind.starts_with("minimax:") {
            return self.minimax.route_model(model_id, route_kind);
        }
        if route_kind.starts_with("mimo:") {
            return self.mimo.route_model(model_id, route_kind);
        }
        self.mimo.route_model(model_id, route_kind)
    }
}

pub fn load_provider() -> anyhow::Result<Box<dyn Provider>> {
    let active = env_str("ACTIVE_PROVIDER", "").to_lowercase();
    match active.as_str() {
        "deepseek" => Ok(Box::new(DeepSeekProvider::new())),
        "kimi" => Ok(Box::new(KimiProvider::new())),
        "mimo" => Ok(Box::new(MiMoProvider::new())),
        "minimax" => Ok(Box::new(MiniMaxProvider::new())),
        "auto" => Ok(Box::new(AutoProvider::new())),
        _ => anyhow::bail!(
            "Unknown ACTIVE_PROVIDER='{}'. Choose one of: deepseek, kimi, mimo, minimax, auto",
            active
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_config() -> BaseProviderConfig {
        BaseProviderConfig {
            name: "test".into(),
            env_prefix: "".into(),
            api_key: "".into(),
            model_primary: "model-primary".into(),
            model_mid: "model-mid".into(),
            model_fast: "model-fast".into(),
            default_max_tokens: 4096,
            min_compat_max_tokens: 16,
            passthrough_metadata: false,
            alias_opus: "opus".into(),
            alias_opus_versioned: "claude-opus-4-5".into(),
            alias_sonnet: "sonnet".into(),
            alias_sonnet_versioned: "claude-sonnet-4-5".into(),
            alias_haiku: "haiku".into(),
            alias_haiku_versioned: "claude-haiku-4-5".into(),
            discovery_max_input_tokens: 1000000,
            discovery_max_tokens: 64000,
            alias_map: {
                let mut m = HashMap::new();
                m.insert("opus".into(), "model-primary".into());
                m.insert("claude-opus-4-5".into(), "model-primary".into());
                m.insert("sonnet".into(), "model-mid".into());
                m.insert("claude-sonnet-4-5".into(), "model-mid".into());
                m.insert("haiku".into(), "model-fast".into());
                m.insert("claude-haiku-4-5".into(), "model-fast".into());
                m.insert("model-primary".into(), "model-primary".into());
                m.insert("model-mid".into(), "model-mid".into());
                m.insert("model-fast".into(), "model-fast".into());
                m
            },
        }
    }

    // --- route_model 测试 ---

    #[test]
    fn route_empty_returns_primary() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("", ""), "model-primary");
    }

    #[test]
    fn route_alias_opus() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("opus", ""), "model-primary");
    }

    #[test]
    fn route_alias_sonnet() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("sonnet", ""), "model-mid");
    }

    #[test]
    fn route_alias_haiku() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("haiku", ""), "model-fast");
    }

    #[test]
    fn route_versioned_alias() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("claude-opus-4-5", ""), "model-primary");
        assert_eq!(cfg.route_model("claude-sonnet-4-5", ""), "model-mid");
        assert_eq!(cfg.route_model("claude-haiku-4-5", ""), "model-fast");
    }

    #[test]
    fn route_prefix_claude_opus() {
        let cfg = make_config();
        assert_eq!(
            cfg.route_model("claude-opus-4-20250514", ""),
            "model-primary"
        );
    }

    #[test]
    fn route_prefix_claude_sonnet() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("claude-sonnet-4-20250514", ""), "model-mid");
    }

    #[test]
    fn route_prefix_claude_haiku() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("claude-haiku-4-20250514", ""), "model-fast");
    }

    #[test]
    fn route_unknown_model_defaults_to_primary() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("gpt-4", ""), "model-primary");
    }

    #[test]
    fn route_self_referential() {
        let cfg = make_config();
        assert_eq!(cfg.route_model("model-primary", ""), "model-primary");
        assert_eq!(cfg.route_model("model-mid", ""), "model-mid");
    }

    #[test]
    fn route_empty_mid_uses_fast() {
        let mut cfg = make_config();
        cfg.model_mid = "".into();
        cfg.alias_map.insert("sonnet".into(), "model-fast".into());
        assert_eq!(cfg.route_model("sonnet", ""), "model-fast");
    }

    // --- AutoProvider key 前缀检测 ---
    // 注意：需要清空环境变量避免 AutoProvider::new() 读到真实 key 干扰测试

    #[test]
    fn auto_detect_deepseek_key() {
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "dk-test123".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "deepseek");
    }

    #[test]
    fn auto_detect_kimi_key() {
        // 清空 deepseek env key 使其不干扰前缀检测
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "sk-kimi-test".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "kimi");
    }

    #[test]
    fn auto_detect_mimo_tp_key() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "tp-test123".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "mimo");
    }

    #[test]
    fn auto_detect_mimo_sk_key() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "sk-mimo-test".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "mimo");
    }

    #[test]
    fn auto_detect_minimax_sk_api_key() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "sk-api-test123".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "minimax");
    }

    #[test]
    fn auto_detect_minimax_sk_cp_key() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "sk-cp-test123".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "minimax");
    }

    #[test]
    fn auto_detect_fallback_mimo_for_generic_sk() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "sk-generic-key".parse().unwrap());
        let (provider, _token) = auto.detect_provider_and_key(&headers).unwrap();
        assert_eq!(provider.name(), "mimo");
    }

    #[test]
    fn auto_detect_no_key_errors() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let headers = HeaderMap::new();
        assert!(auto.detect_provider_and_key(&headers).is_err());
    }

    #[test]
    fn auto_detect_invalid_prefix_errors() {
        std::env::remove_var("DEEPSEEK_API_KEY");
        let auto = AutoProvider::new();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "invalid-key-123".parse().unwrap());
        assert!(auto.detect_provider_and_key(&headers).is_err());
    }

    // --- extract_incoming_token ---

    #[test]
    fn extract_bearer_token() {
        let cfg = make_config();
        let mut headers = HeaderMap::new();
        headers.insert("authorization", "Bearer sk-test123".parse().unwrap());
        assert_eq!(
            cfg.extract_incoming_token(&headers),
            Some("sk-test123".into())
        );
    }

    #[test]
    fn extract_x_api_key() {
        let cfg = make_config();
        let mut headers = HeaderMap::new();
        headers.insert("x-api-key", "sk-test456".parse().unwrap());
        assert_eq!(
            cfg.extract_incoming_token(&headers),
            Some("sk-test456".into())
        );
    }

    #[test]
    fn extract_no_token() {
        let cfg = make_config();
        let headers = HeaderMap::new();
        assert_eq!(cfg.extract_incoming_token(&headers), None);
    }
}
