#![allow(dead_code)]

use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};

pub fn infer_synthetic_block_from_event(event_type: &str, event_data: &Value) -> Value {
    if event_type == "content_block_delta" {
        if let Some(Value::Object(delta)) = event_data.get("delta") {
            if let Some(Value::String(delta_type)) = delta.get("type") {
                if delta_type == "input_json_delta" {
                    let mut out = Map::new();
                    out.insert("type".to_string(), Value::String("tool_use".to_string()));
                    out.insert("id".to_string(), Value::String("".to_string()));
                    out.insert("name".to_string(), Value::String("".to_string()));
                    out.insert("input".to_string(), Value::Object(Map::new()));
                    return Value::Object(out);
                }
            }
        }
        let mut out = Map::new();
        out.insert("type".to_string(), Value::String("text".to_string()));
        out.insert("text".to_string(), Value::String("".to_string()));
        return Value::Object(out);
    }
    if event_type == "content_block_stop" {
        let mut out = Map::new();
        out.insert("type".to_string(), Value::String("text".to_string()));
        out.insert("text".to_string(), Value::String("".to_string()));
        return Value::Object(out);
    }

    let mut out = Map::new();
    out.insert("type".to_string(), Value::String("text".to_string()));
    out.insert("text".to_string(), Value::String("".to_string()));
    Value::Object(out)
}

pub fn normalize_input_json_for_stream(raw: &str) -> (String, String) {
    if raw.trim().is_empty() {
        return ("{}".to_string(), "empty".to_string());
    }

    if let Ok(parsed) = serde_json::from_str::<Value>(raw) {
        return (
            serde_json::to_string(&parsed).unwrap_or_default(),
            "parsed_full".to_string(),
        );
    }

    let stream = serde_json::Deserializer::from_str(raw).into_iter::<Value>();
    let mut fragments = Vec::new();
    for item in stream {
        if let Ok(val) = item {
            fragments.push(val);
        } else {
            fragments.clear();
            break;
        }
    }

    if !fragments.is_empty() {
        let mut merged = fragments.remove(0);
        for obj in fragments.iter() {
            if let (Value::Object(m), Value::Object(o)) = (&mut merged, obj.clone()) {
                for (k, v) in o {
                    m.insert(k, v);
                }
            } else if let (Value::Array(m), Value::Array(o)) = (&mut merged, obj.clone()) {
                m.extend(o);
            } else {
                merged = obj.clone();
            }
        }
        return (
            serde_json::to_string(&merged).unwrap_or_default(),
            format!("merged_fragments:{}", fragments.len() + 1),
        );
    }

    let mut fallback = Map::new();
    fallback.insert("raw".to_string(), Value::String(raw.to_string()));
    (
        serde_json::to_string(&Value::Object(fallback)).unwrap_or_default(),
        "fallback_wrapped_raw".to_string(),
    )
}

pub fn emit_frame(event_name: Option<&str>, data_payload: Option<&str>) -> Vec<u8> {
    let mut out_lines = Vec::new();
    if let Some(en) = event_name {
        out_lines.push(format!("event: {}", en));
    }
    if let Some(dp) = data_payload {
        for segment in dp.split('\n') {
            out_lines.push(format!("data: {}", segment));
        }
    }

    let mut res = out_lines.join("\n");
    res.push_str("\n\n");
    res.into_bytes()
}

pub struct SseState {
    pub seen_block_starts: HashSet<i64>,
    pub block_kind_by_index: HashMap<i64, String>,
    pub tool_partial_json_by_index: HashMap<i64, String>,
    pub pending_event_name: Option<String>,
}

impl SseState {
    pub fn new() -> Self {
        Self {
            seen_block_starts: HashSet::new(),
            block_kind_by_index: HashMap::new(),
            tool_partial_json_by_index: HashMap::new(),
            pending_event_name: None,
        }
    }
}

pub fn process_sse_frame(
    frame_lines: &[String],
    state: &mut SseState,
) -> (Option<String>, Vec<String>) {
    let mut output_data = Vec::new();
    if frame_lines.is_empty() {
        return (None, output_data);
    }

    let mut event_name: Option<String> = None;
    let mut data_lines = Vec::new();

    for line in frame_lines {
        if let Some(rest) = line.strip_prefix("event:") {
            if event_name.is_none() {
                event_name = Some(rest.trim().to_string());
            }
        } else if let Some(rest) = line.strip_prefix("data:") {
            let data_value = rest.strip_prefix(' ').unwrap_or(rest);
            data_lines.push(data_value.to_string());
        }
    }

    if event_name.is_some() && data_lines.is_empty() {
        state.pending_event_name = event_name.clone();
        return (None, output_data);
    }

    if event_name.is_none() && state.pending_event_name.is_some() && !data_lines.is_empty() {
        event_name = state.pending_event_name.clone();
        state.pending_event_name = None;
    }

    if event_name.is_some() && state.pending_event_name.is_some() && !data_lines.is_empty() {
        state.pending_event_name = None;
    }

    if data_lines.is_empty() {
        // 纯文本帧（如注释行），直接透传
        output_data.push(frame_lines.join("\n"));
        return (event_name, output_data);
    }

    let payload = data_lines.join("\n");
    if payload == "[DONE]" {
        output_data.push(payload);
        return (event_name, output_data);
    }

    let parsed_res: Result<Value, _> = serde_json::from_str(&payload);
    let parsed = match parsed_res {
        Ok(v) => v,
        Err(e) => {
            println!(
                "[gateway malformed sse data] Error: {}; payload_len={}",
                e,
                payload.len()
            );
            let mut error_obj = Map::new();
            error_obj.insert("type".to_string(), Value::String("error".to_string()));
            let mut err_inner = Map::new();
            err_inner.insert(
                "type".to_string(),
                Value::String("gateway_bad_event".to_string()),
            );
            err_inner.insert(
                "message".to_string(),
                Value::String("Dropped malformed upstream data event".to_string()),
            );
            error_obj.insert("error".to_string(), Value::Object(err_inner));
            return (
                Some("error".to_string()),
                vec![serde_json::to_string(&error_obj).unwrap()],
            );
        }
    };

    let event_type = parsed.get("type").and_then(|v| v.as_str());
    let event_index = parsed.get("index").and_then(|v| v.as_i64());

    if let (Some(ev_type), Some(ev_index)) = (event_type, event_index) {
        if (ev_type == "content_block_delta" || ev_type == "content_block_stop")
            && !state.seen_block_starts.contains(&ev_index)
        {
            let synthetic_block = infer_synthetic_block_from_event(ev_type, &parsed);
            let mut synthetic_payload = Map::new();
            synthetic_payload.insert(
                "type".to_string(),
                Value::String("content_block_start".to_string()),
            );
            synthetic_payload.insert(
                "index".to_string(),
                Value::Number(serde_json::Number::from(ev_index)),
            );

            let block_type = synthetic_block
                .get("type")
                .and_then(|v| v.as_str())
                .unwrap_or("text")
                .to_string();
            synthetic_payload.insert("content_block".to_string(), synthetic_block);

            println!(
                "[gateway sse shim] synthetic_start=1 trigger_event={} index={} synthetic_type={}",
                ev_type, ev_index, block_type
            );

            output_data.push(serde_json::to_string(&synthetic_payload).unwrap());
            state.seen_block_starts.insert(ev_index);
            state.block_kind_by_index.insert(ev_index, block_type);
        } else if ev_type == "content_block_start" {
            state.seen_block_starts.insert(ev_index);
            if let Some(Value::Object(cb)) = parsed.get("content_block") {
                if let Some(Value::String(cb_type)) = cb.get("type") {
                    state.block_kind_by_index.insert(ev_index, cb_type.clone());
                }
            }
        }
    }

    if let (Some(ev_type), Some(ev_index)) = (event_type, event_index) {
        if ev_type == "content_block_delta" {
            if let Some(kind) = state.block_kind_by_index.get(&ev_index) {
                if kind == "tool_use" {
                    if let Some(Value::Object(delta)) = parsed.get("delta") {
                        if delta.get("type").and_then(|v| v.as_str()) == Some("input_json_delta") {
                            if let Some(Value::String(partial_json)) = delta.get("partial_json") {
                                let entry = state
                                    .tool_partial_json_by_index
                                    .entry(ev_index)
                                    .or_default();
                                entry.push_str(partial_json);

                                println!("[gateway sse shim] buffered_tool_input_delta=1 index={} chunk_len={} total_len={}",
                                    ev_index, partial_json.len(), entry.len());
                                return (event_name, output_data);
                            }
                        }
                    }
                }
            }
        }
    }

    if let (Some(ev_type), Some(ev_index)) = (event_type, event_index) {
        if ev_type == "content_block_stop" {
            if let Some(kind) = state.block_kind_by_index.get(&ev_index) {
                if kind == "tool_use" {
                    if let Some(buffered) = state.tool_partial_json_by_index.get(&ev_index) {
                        let (normalized_json, normalize_reason) =
                            normalize_input_json_for_stream(buffered);

                        let mut shim_delta_payload = Map::new();
                        shim_delta_payload.insert(
                            "type".to_string(),
                            Value::String("content_block_delta".to_string()),
                        );
                        shim_delta_payload.insert(
                            "index".to_string(),
                            Value::Number(serde_json::Number::from(ev_index)),
                        );

                        let mut delta_inner = Map::new();
                        delta_inner.insert(
                            "type".to_string(),
                            Value::String("input_json_delta".to_string()),
                        );
                        delta_inner.insert(
                            "partial_json".to_string(),
                            Value::String(normalized_json.clone()),
                        );
                        shim_delta_payload.insert("delta".to_string(), Value::Object(delta_inner));

                        println!("[gateway sse shim] emitted_normalized_tool_input_delta=1 index={} reason={} normalized_len={}",
                            ev_index, normalize_reason, normalized_json.len());

                        output_data.push(serde_json::to_string(&shim_delta_payload).unwrap());
                        state.tool_partial_json_by_index.remove(&ev_index);
                    }
                }
            }
        }
    }

    output_data.push(payload);
    (event_name, output_data)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn frame(lines: &[&str]) -> Vec<String> {
        lines.iter().map(|s| s.to_string()).collect()
    }

    // --- 基础帧解析 ---

    #[test]
    fn empty_frame_returns_nothing() {
        let mut state = SseState::new();
        let (name, data) = process_sse_frame(&[], &mut state);
        assert!(name.is_none());
        assert!(data.is_empty());
    }

    #[test]
    fn event_only_frame_stores_pending_name() {
        let mut state = SseState::new();
        let f = frame(&["event: message_start"]);
        let (name, data) = process_sse_frame(&f, &mut state);
        assert!(name.is_none());
        assert!(data.is_empty());
        assert_eq!(state.pending_event_name, Some("message_start".into()));
    }

    #[test]
    fn data_frame_with_pending_event() {
        let mut state = SseState::new();
        state.pending_event_name = Some("content_block_start".into());
        let payload =
            json!({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}});
        let f = frame(&[&format!("data: {}", payload)]);
        let (name, data) = process_sse_frame(&f, &mut state);
        assert_eq!(name, Some("content_block_start".into()));
        assert_eq!(data.len(), 1);
        assert!(state.pending_event_name.is_none());
    }

    #[test]
    fn done_frame() {
        let mut state = SseState::new();
        let f = frame(&["data: [DONE]"]);
        let (name, data) = process_sse_frame(&f, &mut state);
        assert_eq!(data, vec!["[DONE]"]);
    }

    #[test]
    fn malformed_json_emits_error_event() {
        let mut state = SseState::new();
        let f = frame(&["data: {not valid json"]);
        let (name, data) = process_sse_frame(&f, &mut state);
        assert_eq!(name, Some("error".into()));
        assert_eq!(data.len(), 1);
        let parsed: Value = serde_json::from_str(&data[0]).unwrap();
        assert_eq!(parsed["type"], "error");
    }

    // --- 合成 content_block_start ---

    #[test]
    fn delta_without_prior_start_synthesizes_block() {
        let mut state = SseState::new();
        let payload = json!({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello"}
        });
        let f = frame(&[&format!("data: {}", payload)]);
        let (_name, data) = process_sse_frame(&f, &mut state);
        // 应该产生合成 start + 原始 delta
        assert!(
            data.len() >= 2,
            "expected synthetic start + delta, got {}",
            data.len()
        );
        let start: Value = serde_json::from_str(&data[0]).unwrap();
        assert_eq!(start["type"], "content_block_start");
        assert_eq!(start["index"], 0);
        assert!(state.seen_block_starts.contains(&0));
    }

    #[test]
    fn stop_without_prior_start_synthesizes_block() {
        let mut state = SseState::new();
        let payload = json!({
            "type": "content_block_stop",
            "index": 1
        });
        let f = frame(&[&format!("data: {}", payload)]);
        let (_name, data) = process_sse_frame(&f, &mut state);
        assert!(data.len() >= 2);
        let start: Value = serde_json::from_str(&data[0]).unwrap();
        assert_eq!(start["type"], "content_block_start");
        assert_eq!(start["index"], 1);
    }

    #[test]
    fn delta_with_prior_start_no_synthetic() {
        let mut state = SseState::new();
        state.seen_block_starts.insert(0);
        state.block_kind_by_index.insert(0, "text".into());
        let payload = json!({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hi"}
        });
        let f = frame(&[&format!("data: {}", payload)]);
        let (_name, data) = process_sse_frame(&f, &mut state);
        // 只有原始 payload，无合成 start
        assert_eq!(data.len(), 1);
    }

    // --- Tool input 缓冲 ---

    #[test]
    fn tool_use_delta_is_buffered() {
        let mut state = SseState::new();
        state.seen_block_starts.insert(0);
        state.block_kind_by_index.insert(0, "tool_use".into());

        let payload = json!({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "{\"q\":"}
        });
        let f = frame(&[&format!("data: {}", payload)]);
        let (_name, data) = process_sse_frame(&f, &mut state);
        // 被缓冲，不产生输出
        assert!(
            data.is_empty(),
            "tool delta should be buffered, got {:?}",
            data
        );
        assert_eq!(
            state.tool_partial_json_by_index.get(&0),
            Some(&"{\"q\":".to_string())
        );
    }

    #[test]
    fn tool_use_stop_emits_normalized_input() {
        let mut state = SseState::new();
        state.seen_block_starts.insert(0);
        state.block_kind_by_index.insert(0, "tool_use".into());
        state
            .tool_partial_json_by_index
            .insert(0, "{\"query\":\"test\"}".into());

        let payload = json!({
            "type": "content_block_stop",
            "index": 0
        });
        let f = frame(&[&format!("data: {}", payload)]);
        let (_name, data) = process_sse_frame(&f, &mut state);
        // 应产生 normalized delta + stop
        assert!(data.len() >= 2, "expected delta + stop, got {}", data.len());
        let delta: Value = serde_json::from_str(&data[0]).unwrap();
        assert_eq!(delta["type"], "content_block_delta");
        assert_eq!(delta["delta"]["type"], "input_json_delta");
        // buffer 应被清理
        assert!(state.tool_partial_json_by_index.get(&0).is_none());
    }

    #[test]
    fn tool_use_multiple_deltas_concatenated() {
        let mut state = SseState::new();
        state.seen_block_starts.insert(0);
        state.block_kind_by_index.insert(0, "tool_use".into());

        // 第一个 delta
        let p1 = json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"q\":"}});
        process_sse_frame(&frame(&[&format!("data: {}", p1)]), &mut state);

        // 第二个 delta
        let p2 = json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"test\"}"}});
        process_sse_frame(&frame(&[&format!("data: {}", p2)]), &mut state);

        assert_eq!(
            state.tool_partial_json_by_index.get(&0),
            Some(&"{\"q\":\"test\"}".to_string())
        );
    }

    // --- Event name 传递 ---

    #[test]
    fn event_name_propagated_to_output() {
        let mut state = SseState::new();
        state.pending_event_name = Some("message_start".into());
        let payload = json!({"type": "message_start", "message": {}});
        let f = frame(&[&format!("data: {}", payload)]);
        let (name, _data) = process_sse_frame(&f, &mut state);
        assert_eq!(name, Some("message_start".into()));
    }
}
