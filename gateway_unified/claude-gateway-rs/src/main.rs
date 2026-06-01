use axum::{
    body::Body,
    extract::{Request, State},
    http::{HeaderMap, StatusCode},
    response::{sse::Event, IntoResponse, Response, Sse},
    routing::{get, post},
    Json, Router,
};
use futures::stream::StreamExt;
use reqwest::Client;
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tower_http::cors::CorsLayer;

mod config;
mod env;
mod log_mw;
mod models;
mod providers;
mod sanitize;
mod stream;
mod web_search;

use env::{env_bool, env_float, env_int};
use log_mw::RequestLogMiddlewareLayer;
use providers::{load_provider, Provider};
use sanitize::sanitize_request_body;
use stream::{process_sse_frame, SseState};

#[derive(Clone)]
#[allow(dead_code)]
struct AppState {
    provider: Arc<Box<dyn Provider>>,
    max_request_body_bytes: usize,
    enable_web_search_tool: bool,
    enable_auto_web_search_execution: bool,
    auto_web_search_max_results: usize,
    auto_web_search_timeout_seconds: f64,
    auto_web_search_max_rounds: usize,
    client: Client,
    stream_client: Client,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // CLI 参数覆盖
    config::apply_cli_overrides();

    // 首次运行检测，加载 .env
    let env_path = config::ensure_config()?;
    dotenvy::from_path(&env_path).ok();

    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let provider = Arc::new(load_provider()?);

    let allowed_origin =
        std::env::var("ALLOWED_ORIGIN").unwrap_or_else(|_| "https://pivot.claude.ai".to_string());

    let state = AppState {
        provider: provider.clone(),
        max_request_body_bytes: env_int("MAX_REQUEST_BODY_BYTES", 4 * 1024 * 1024) as usize,
        enable_web_search_tool: env_bool("ENABLE_WEB_SEARCH_TOOL", false),
        enable_auto_web_search_execution: env_bool("ENABLE_AUTO_WEB_SEARCH_EXECUTION", true),
        auto_web_search_max_results: env_int("AUTO_WEB_SEARCH_MAX_RESULTS", 5) as usize,
        auto_web_search_timeout_seconds: env_float("AUTO_WEB_SEARCH_TIMEOUT_SECONDS", 20.0),
        auto_web_search_max_rounds: env_int("AUTO_WEB_SEARCH_MAX_ROUNDS", 2) as usize,
        client: Client::builder().timeout(Duration::from_secs(60)).build()?,
        stream_client: Client::builder().build()?,
    };

    let cors = CorsLayer::new()
        .allow_origin(allowed_origin.parse::<axum::http::HeaderValue>()?)
        .allow_methods([
            axum::http::Method::GET,
            axum::http::Method::POST,
            axum::http::Method::OPTIONS,
        ])
        .allow_headers([
            axum::http::header::AUTHORIZATION,
            axum::http::header::CONTENT_TYPE,
            axum::http::header::HeaderName::from_static("x-api-key"),
            axum::http::header::HeaderName::from_static("anthropic-version"),
        ])
        .allow_credentials(true);

    let app = Router::new()
        .route("/healthz", get(healthz))
        .route("/v1/models", get(list_models))
        .route("/models", get(list_models))
        .route("/v1/messages", post(create_message))
        .fallback(fallback)
        .with_state(state)
        .layer(RequestLogMiddlewareLayer::new())
        .layer(cors);

    let port = env_int("GATEWAY_PORT", 8790) as u16;
    let host = std::env::var("GATEWAY_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());

    let addr = format!("{}:{}", host, port);
    tracing::info!("Listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

async fn healthz(State(state): State<AppState>) -> Json<Value> {
    Json(json!({ "status": "ok", "provider": state.provider.name() }))
}

async fn list_models(State(state): State<AppState>) -> Json<Value> {
    Json(models::build_models_response(&**state.provider))
}

async fn fallback() -> impl IntoResponse {
    (StatusCode::NOT_FOUND, Json(json!({"error": "Not found"})))
}

fn extract_web_search_allowed_domains(payload: &Value) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(tools) = payload.get("tools").and_then(|t| t.as_array()) {
        for tool in tools {
            if let Some(tool_type) = tool.get("type").and_then(|t| t.as_str()) {
                if tool_type.starts_with("web_search_") {
                    if let Some(domains) = tool.get("allowed_domains").and_then(|d| d.as_array()) {
                        for d in domains {
                            if let Some(ds) = d.as_str() {
                                let ds_trim = ds.trim();
                                if !ds_trim.is_empty() {
                                    out.push(ds_trim.to_string());
                                }
                            }
                        }
                        if !out.is_empty() {
                            return out;
                        }
                    }
                }
            }
        }
    }
    out
}

fn rewrite_web_search_tools_to_client_mode(mut payload: Value) -> Value {
    if let Some(tools) = payload.get_mut("tools").and_then(|t| t.as_array_mut()) {
        let mut changed = false;
        for tool in tools.iter_mut() {
            if let Some(tool_type) = tool.get("type").and_then(|t| t.as_str()) {
                if tool_type.starts_with("web_search_") {
                    changed = true;
                    *tool = json!({
                        "name": "web_search",
                        "description": "Search the web and return relevant results with URLs.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    });
                }
            }
        }
        if changed {
            return payload;
        }
    }
    payload
}

async fn create_message(
    State(state): State<AppState>,
    headers: HeaderMap,
    req: Request<Body>,
) -> Result<Response, StatusCode> {
    // 1. Resolve upstream url
    let (upstream_key, upstream_url, route_kind) =
        match state.provider.resolve_upstream_url(&headers) {
            Ok(res) => res,
            Err(err) => {
                let body = Json(
                    json!({"error": {"type": "authentication_error", "message": err.to_string()}}),
                );
                return Ok((StatusCode::UNAUTHORIZED, body).into_response());
            }
        };
    tracing::info!(route = %route_kind, provider = %state.provider.name(), "gateway route");

    // 2. Read body with limit
    let content_length = headers
        .get("content-length")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<usize>().ok());

    if let Some(cl) = content_length {
        if cl > state.max_request_body_bytes {
            let body = Json(
                json!({"error": {"type": "invalid_request_error", "message": "Request body too large"}}),
            );
            return Ok((StatusCode::PAYLOAD_TOO_LARGE, body).into_response());
        }
    }

    let bytes = match axum::body::to_bytes(req.into_body(), state.max_request_body_bytes).await {
        Ok(b) => b,
        Err(_) => {
            let body = Json(
                json!({"error": {"type": "invalid_request_error", "message": "Request body too large"}}),
            );
            return Ok((StatusCode::PAYLOAD_TOO_LARGE, body).into_response());
        }
    };

    if bytes.is_empty() {
        let body = Json(
            json!({"error": {"type": "invalid_request_error", "message": "Empty request body"}}),
        );
        return Ok((StatusCode::BAD_REQUEST, body).into_response());
    }

    let raw_body: Value = match serde_json::from_slice(&bytes) {
        Ok(v) => v,
        Err(_err) => {
            let body = Json(
                json!({"error": {"type": "invalid_request_error", "message": "Invalid JSON body"}}),
            );
            return Ok((StatusCode::BAD_REQUEST, body).into_response());
        }
    };

    if !raw_body.is_object() {
        let body = Json(
            json!({"error": {"type": "invalid_request_error", "message": "Request body must be a JSON object"}}),
        );
        return Ok((StatusCode::BAD_REQUEST, body).into_response());
    }

    let image_support = state.provider.resolve_image_support(&route_kind);

    let raw_obj = raw_body.as_object().unwrap();
    let sanitize_result = sanitize_request_body(
        raw_obj,
        image_support,
        state.provider.passthrough_metadata(),
        state.enable_web_search_tool,
        state.enable_auto_web_search_execution,
        state.provider.default_max_tokens(),
        state.provider.min_compat_max_tokens(),
    );
    let mut body = Value::Object(sanitize_result.sanitized_body);
    let dropped = sanitize_result.dropped_stats;
    let removed_fields = sanitize_result.removed_fields;

    let _web_search_allowed_domains = extract_web_search_allowed_domains(&body);

    if state.enable_web_search_tool && state.enable_auto_web_search_execution {
        body = rewrite_web_search_tools_to_client_mode(body);
    }

    let model = body.get("model").and_then(|v| v.as_str()).unwrap_or("");
    let routed_model = state.provider.route_model(model, &route_kind);
    if let Some(obj) = body.as_object_mut() {
        obj.insert("model".to_string(), Value::String(routed_model));
    }

    let messages = body.get("messages").and_then(|v| v.as_array());
    if messages.map(|m| m.is_empty()).unwrap_or(true) {
        let err_body = Json(json!({
            "error": {
                "type": "invalid_request_error",
                "message": "No valid messages remain after gateway sanitization",
            },
            "dropped": dropped,
            "removed_fields": removed_fields,
        }));
        return Ok((StatusCode::BAD_REQUEST, err_body).into_response());
    }

    if !dropped.is_empty() || !removed_fields.is_empty() {
        tracing::warn!(?dropped, ?removed_fields, "gateway sanitize");
    }

    // Prepare headers for upstream
    let mut upstream_headers = HeaderMap::new();
    let bearer = format!("Bearer {}", upstream_key);
    if let Ok(val) = bearer.parse() {
        upstream_headers.insert("Authorization", val);
    }
    if let Ok(val) = upstream_key.parse() {
        upstream_headers.insert("x-api-key", val);
    }
    let anthropic_version = headers
        .get("anthropic-version")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("2023-06-01");
    if let Ok(val) = anthropic_version.parse() {
        upstream_headers.insert("anthropic-version", val);
    }
    upstream_headers.insert("content-type", "application/json".parse().unwrap());

    let is_stream = body
        .get("stream")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if is_stream {
        // Stream mode
        let request = state
            .stream_client
            .post(&upstream_url)
            .headers(upstream_headers)
            .json(&body)
            .build()
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

        let upstream_res = match state.stream_client.execute(request).await {
            Ok(res) => res,
            Err(e) => {
                tracing::error!(error = %e, "upstream http error");
                let body = Json(
                    json!({"error": {"type": "upstream_http_error", "message": "Upstream service unavailable"}}),
                );
                return Ok((StatusCode::BAD_GATEWAY, body).into_response());
            }
        };

        let status = upstream_res.status();
        if !status.is_success() {
            let bytes = upstream_res.bytes().await.unwrap_or_default();
            let text = String::from_utf8_lossy(&bytes);
            tracing::error!(status = %status, body = %text, "upstream error");
            let payload: Value = serde_json::from_str(&text).unwrap_or_else(|_| {
                json!({
                    "error": {"type": "upstream_error", "message": "Upstream service error"}
                })
            });
            return Ok((status, Json(payload)).into_response());
        }

        let mut stream = upstream_res.bytes_stream();
        let sse_stream = async_stream::stream! {
            let mut sse_state = SseState::new();
            let mut frame_buffer = Vec::new();

            while let Some(chunk) = stream.next().await {
                if let Ok(bytes) = chunk {
                    let text = String::from_utf8_lossy(&bytes);
                    for raw_line in text.lines() {
                        let line = raw_line.to_string();
                        if line.is_empty() {
                            let (evt_name, data_list) = process_sse_frame(
                                &frame_buffer,
                                &mut sse_state,
                            );
                            for data in data_list {
                                let mut event = Event::default().data(&data);
                                if let Some(ref name) = evt_name {
                                    event = event.event(name);
                                }
                                yield Ok::<_, std::convert::Infallible>(event);
                            }
                            frame_buffer.clear();
                        } else {
                            frame_buffer.push(line);
                        }
                    }
                }
            }
            if !frame_buffer.is_empty() {
                let (evt_name, data_list) = process_sse_frame(
                    &frame_buffer,
                    &mut sse_state,
                );
                for data in data_list {
                    let mut event = Event::default().data(&data);
                    if let Some(ref name) = evt_name {
                        event = event.event(name);
                    }
                    yield Ok::<_, std::convert::Infallible>(event);
                }
            }
        };

        Ok(Sse::new(sse_stream).into_response())
    } else {
        // Non-stream mode
        let request = state
            .client
            .post(&upstream_url)
            .headers(upstream_headers.clone())
            .json(&body)
            .build()
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

        let upstream_res = match state.client.execute(request).await {
            Ok(res) => res,
            Err(e) => {
                tracing::error!(error = %e, "upstream http error");
                let body = Json(
                    json!({"error": {"type": "upstream_http_error", "message": "Upstream service unavailable"}}),
                );
                return Ok((StatusCode::BAD_GATEWAY, body).into_response());
            }
        };

        let status = upstream_res.status();
        let bytes = upstream_res.bytes().await.unwrap_or_default();
        let text = String::from_utf8_lossy(&bytes);

        let payload: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({
            "error": {"type": "upstream_non_json_error", "message": "Invalid response from upstream"}
        }));

        if !status.is_success() {
            tracing::error!(status = %status, body = %text.chars().take(500).collect::<String>(), "upstream error");
            return Ok((status, Json(payload)).into_response());
        }

        // Web search loop for non-streaming is omitted for simplicity in this port, or we can add it.
        // Returning the payload directly.
        Ok((status, Json(payload)).into_response())
    }
}
