use crate::providers::Provider;
use chrono::Utc;
use serde_json::json;
use std::time::{SystemTime, UNIX_EPOCH};

pub fn build_models_response(provider: &dyn Provider) -> serde_json::Value {
    let now = Utc::now();
    let now_iso = now
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        .replace("+00:00", "Z");
    let now_unix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();

    let default_capabilities = json!({
        "batch": {"supported": false},
        "citations": {"supported": false},
        "code_execution": {"supported": false},
        "context_management": {"supported": false},
        "effort": {"supported": false},
        "image_input": {"supported": provider.image_support()},
        "pdf_input": {"supported": false},
        "structured_outputs": {"supported": true},
        "thinking": {"supported": false},
    });

    let non_empty = |value: &str, fallback: &str| -> String {
        let text = value.trim();
        if text.is_empty() {
            fallback.to_string()
        } else {
            text.to_string()
        }
    };

    let model_info = |model_id: String, display_name: &str| -> serde_json::Value {
        json!({
            "type": "model",
            "object": "model",
            "id": model_id,
            "name": display_name,
            "display_name": display_name,
            "created_at": now_iso,
            "created": now_unix,
            "max_input_tokens": provider.discovery_max_input_tokens(),
            "max_tokens": provider.discovery_max_tokens(),
            "context_window": provider.discovery_max_input_tokens(),
            "max_output_tokens": provider.discovery_max_tokens(),
            "capabilities": default_capabilities,
        })
    };

    let ordered_ids = vec![
        (
            non_empty(provider.alias_opus_versioned(), "claude-opus-4-5"),
            "Claude Opus 4.5",
        ),
        (
            non_empty(provider.alias_sonnet_versioned(), "claude-sonnet-4-5"),
            "Claude Sonnet 4.5",
        ),
        (non_empty(provider.alias_opus(), "opus"), "Claude Opus 4.5"),
        (
            non_empty(provider.alias_sonnet(), "sonnet"),
            "Claude Sonnet 4.5",
        ),
    ];

    let mut seen_ids = std::collections::HashSet::new();
    let mut model_data = Vec::new();
    let mut first_id = String::new();
    let mut last_id = String::new();

    for (model_id, display_name) in ordered_ids {
        if seen_ids.contains(&model_id) {
            continue;
        }
        seen_ids.insert(model_id.clone());
        if first_id.is_empty() {
            first_id = model_id.clone();
        }
        last_id = model_id.clone();
        model_data.push(model_info(model_id, display_name));
    }

    json!({
        "object": "list",
        "data": model_data,
        "first_id": first_id,
        "last_id": last_id,
        "has_more": false,
    })
}
