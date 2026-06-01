use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};

const MAX_TEXT_BLOCK_LEN: usize = 200_000;
const MAX_IMAGE_BASE64_LEN: usize = 10_000_000;
const MAX_TOOL_DESC_LEN: usize = 8_000;
const MAX_TOOL_INPUT_DEPTH: usize = 10;
const MAX_MESSAGES: usize = 1000;

const WEB_SEARCH_TOOL_TYPE_PREFIX: &str = "web_search_";

fn get_web_search_allowed_tool_types() -> HashSet<&'static str> {
    let mut s = HashSet::new();
    s.insert("web_search_20250305");
    s
}

fn get_web_search_content_block_types() -> HashSet<&'static str> {
    let mut s = HashSet::new();
    s.insert("server_tool_use");
    s.insert("web_search_tool_result");
    s
}

fn get_default_top_level_allowlist() -> HashSet<&'static str> {
    let mut s = HashSet::new();
    s.insert("model");
    s.insert("max_tokens");
    s.insert("messages");
    s.insert("stop_sequences");
    s.insert("stream");
    s.insert("system");
    s.insert("temperature");
    s.insert("thinking");
    s.insert("output_config");
    s.insert("top_p");
    s.insert("tools");
    s.insert("tool_choice");
    s
}

fn get_base_supported_content_block_types() -> HashSet<&'static str> {
    let mut s = HashSet::new();
    s.insert("text");
    s.insert("thinking");
    s.insert("tool_use");
    s.insert("tool_result");
    s
}

fn get_unsupported_content_block_types() -> HashSet<&'static str> {
    let mut s = HashSet::new();
    s.insert("document");
    s.insert("search_result");
    s.insert("redacted_thinking");
    s.insert("server_tool_use");
    s.insert("web_search_tool_result");
    s.insert("code_execution_tool_result");
    s.insert("mcp_tool_use");
    s.insert("mcp_tool_result");
    s.insert("container_upload");
    s
}

pub fn get_supported_content_types(
    image_support: bool,
    allow_server_web_search_blocks: bool,
) -> HashSet<&'static str> {
    let mut types = get_base_supported_content_block_types();
    if image_support {
        types.insert("image");
        types.insert("image_url");
    }
    if allow_server_web_search_blocks {
        types.extend(get_web_search_content_block_types());
    }
    types
}

pub fn normalize_system(system_value: &Value) -> Value {
    if let Value::String(s) = system_value {
        return Value::String(s.clone());
    }

    if let Value::Array(arr) = system_value {
        let mut blocks = Vec::new();
        for item in arr {
            if let Value::String(s) = item {
                if !s.trim().is_empty() {
                    let mut block = Map::new();
                    block.insert("type".to_string(), Value::String("text".to_string()));
                    block.insert("text".to_string(), Value::String(s.clone()));
                    blocks.push(Value::Object(block));
                }
                continue;
            }

            if let Value::Object(obj) = item {
                if let Some(Value::String(t)) = obj.get("type") {
                    if t == "text" {
                        if let Some(Value::String(text)) = obj.get("text") {
                            if !text.trim().is_empty() {
                                let mut block = Map::new();
                                block.insert("type".to_string(), Value::String("text".to_string()));
                                block.insert("text".to_string(), Value::String(text.clone()));
                                blocks.push(Value::Object(block));
                            }
                        }
                    }
                }
            }
        }
        return Value::Array(blocks);
    }

    if let Value::Object(obj) = system_value {
        if let Some(Value::String(t)) = obj.get("type") {
            if t == "text" {
                if let Some(Value::String(text)) = obj.get("text") {
                    let mut block = Map::new();
                    block.insert("type".to_string(), Value::String("text".to_string()));
                    block.insert("text".to_string(), Value::String(text.clone()));
                    return Value::Array(vec![Value::Object(block)]);
                }
            }
        }
    }

    // Default fallback, turn it into string
    match system_value {
        Value::Null => Value::String("null".to_string()),
        Value::Bool(b) => Value::String(b.to_string()),
        Value::Number(n) => Value::String(n.to_string()),
        _ => Value::String(system_value.to_string()),
    }
}

fn dict_nesting_depth(obj: &Value, current: usize) -> usize {
    match obj {
        Value::Object(map) => {
            if map.is_empty() {
                return current;
            }
            map.values()
                .map(|v| dict_nesting_depth(v, current + 1))
                .max()
                .unwrap_or(current)
        }
        Value::Array(arr) => {
            if arr.is_empty() {
                return current;
            }
            arr.iter()
                .map(|v| dict_nesting_depth(v, current + 1))
                .max()
                .unwrap_or(current)
        }
        _ => current,
    }
}

pub fn sanitize_content_block(
    item: &Value,
    dropped: &mut HashMap<String, usize>,
    supported_types: &HashSet<&'static str>,
    allow_server_web_search_blocks: bool,
) -> Option<Value> {
    if let Value::String(s) = item {
        if !s.trim().is_empty() {
            let mut block = Map::new();
            block.insert("type".to_string(), Value::String("text".to_string()));
            block.insert("text".to_string(), Value::String(s.clone()));
            return Some(Value::Object(block));
        }
        *dropped.entry("empty_string_block".to_string()).or_insert(0) += 1;
        return None;
    }

    let obj = match item {
        Value::Object(o) => o,
        _ => {
            *dropped.entry("non_dict_block".to_string()).or_insert(0) += 1;
            return None;
        }
    };

    let block_type = match obj.get("type") {
        Some(Value::String(s)) => s.as_str(),
        _ => {
            *dropped.entry("missing_block_type".to_string()).or_insert(0) += 1;
            return None;
        }
    };

    let unsupported = get_unsupported_content_block_types();
    if unsupported.contains(block_type)
        && !(allow_server_web_search_blocks
            && get_web_search_content_block_types().contains(block_type))
    {
        let key = format!("unsupported_block:{}", block_type);
        *dropped.entry(key).or_insert(0) += 1;
        return None;
    }

    if !supported_types.contains(block_type) {
        let key = format!("unknown_block:{}", block_type);
        *dropped.entry(key).or_insert(0) += 1;
        return None;
    }

    if block_type == "text" {
        if let Some(Value::String(text)) = obj.get("text") {
            if text.trim().is_empty() {
                *dropped.entry("empty_text_block".to_string()).or_insert(0) += 1;
                return None;
            }
            let mut text_val = text.clone();
            if text_val.len() > MAX_TEXT_BLOCK_LEN {
                text_val.truncate(MAX_TEXT_BLOCK_LEN);
                *dropped
                    .entry("truncated_text_block".to_string())
                    .or_insert(0) += 1;
            }
            let mut out = Map::new();
            out.insert("type".to_string(), Value::String("text".to_string()));
            out.insert("text".to_string(), Value::String(text_val));
            return Some(Value::Object(out));
        } else {
            *dropped.entry("invalid_text_block".to_string()).or_insert(0) += 1;
            return None;
        }
    }

    if block_type == "thinking" {
        let thinking_val = obj.get("thinking").and_then(|v| v.as_str());
        let signature = obj.get("signature").and_then(|v| v.as_str());
        if thinking_val.is_none() || thinking_val.unwrap().trim().is_empty() {
            if let Some(Value::String(fallback_text)) = obj.get("text") {
                if !fallback_text.trim().is_empty() {
                    *dropped
                        .entry("invalid_thinking_block_downgraded".to_string())
                        .or_insert(0) += 1;
                    let mut out = Map::new();
                    out.insert("type".to_string(), Value::String("text".to_string()));
                    out.insert("text".to_string(), Value::String(fallback_text.clone()));
                    return Some(Value::Object(out));
                }
            }
            *dropped
                .entry("invalid_thinking_block".to_string())
                .or_insert(0) += 1;
            return None;
        }
        let mut out = Map::new();
        out.insert("type".to_string(), Value::String("thinking".to_string()));
        out.insert(
            "thinking".to_string(),
            Value::String(thinking_val.unwrap().to_string()),
        );
        if let Some(sig) = signature {
            if !sig.is_empty() {
                out.insert("signature".to_string(), Value::String(sig.to_string()));
            }
        }
        return Some(Value::Object(out));
    }

    if block_type == "image" {
        let source = obj.get("source").and_then(|v| v.as_object());
        if source.is_none() {
            *dropped
                .entry("invalid_image_source".to_string())
                .or_insert(0) += 1;
            return None;
        }
        let source = source.unwrap();
        let source_type = source.get("type").and_then(|v| v.as_str());

        if source_type == Some("base64") {
            let media_type = source.get("media_type").and_then(|v| v.as_str());
            let data = source.get("data").and_then(|v| v.as_str());

            if media_type.is_none() || media_type.unwrap().trim().is_empty() {
                *dropped
                    .entry("invalid_image_media_type".to_string())
                    .or_insert(0) += 1;
                return None;
            }
            if data.is_none() || data.unwrap().trim().is_empty() {
                *dropped.entry("invalid_image_data".to_string()).or_insert(0) += 1;
                return None;
            }
            if data.unwrap().len() > MAX_IMAGE_BASE64_LEN {
                *dropped
                    .entry("oversized_image_base64".to_string())
                    .or_insert(0) += 1;
                return None;
            }

            let mut out_source = Map::new();
            out_source.insert("type".to_string(), Value::String("base64".to_string()));
            out_source.insert(
                "media_type".to_string(),
                Value::String(media_type.unwrap().to_string()),
            );
            out_source.insert("data".to_string(), Value::String(data.unwrap().to_string()));

            let mut out = Map::new();
            out.insert("type".to_string(), Value::String("image".to_string()));
            out.insert("source".to_string(), Value::Object(out_source));
            return Some(Value::Object(out));
        }

        if source_type == Some("url") {
            let url = source.get("url").and_then(|v| v.as_str());
            if url.is_none() || url.unwrap().trim().is_empty() {
                *dropped.entry("invalid_image_url".to_string()).or_insert(0) += 1;
                return None;
            }

            let mut out_source = Map::new();
            out_source.insert("type".to_string(), Value::String("url".to_string()));
            out_source.insert("url".to_string(), Value::String(url.unwrap().to_string()));

            let mut out = Map::new();
            out.insert("type".to_string(), Value::String("image".to_string()));
            out.insert("source".to_string(), Value::Object(out_source));
            return Some(Value::Object(out));
        }

        *dropped
            .entry("unsupported_image_source_type".to_string())
            .or_insert(0) += 1;
        return None;
    }

    if block_type == "image_url" {
        let image_url = obj.get("image_url");
        if let Some(Value::Object(url_obj)) = image_url {
            let url = url_obj.get("url").and_then(|v| v.as_str());
            let detail = url_obj.get("detail").and_then(|v| v.as_str());

            if url.is_none() || url.unwrap().trim().is_empty() {
                *dropped
                    .entry("invalid_image_url_block".to_string())
                    .or_insert(0) += 1;
                return None;
            }

            let mut out_image_url = Map::new();
            out_image_url.insert("url".to_string(), Value::String(url.unwrap().to_string()));
            if let Some(d) = detail {
                if !d.is_empty() {
                    out_image_url.insert("detail".to_string(), Value::String(d.to_string()));
                }
            }

            let mut out = Map::new();
            out.insert("type".to_string(), Value::String("image_url".to_string()));
            out.insert("image_url".to_string(), Value::Object(out_image_url));
            return Some(Value::Object(out));
        }

        if let Some(Value::String(s)) = image_url {
            if !s.trim().is_empty() {
                let mut out_image_url = Map::new();
                out_image_url.insert("url".to_string(), Value::String(s.clone()));
                let mut out = Map::new();
                out.insert("type".to_string(), Value::String("image_url".to_string()));
                out.insert("image_url".to_string(), Value::Object(out_image_url));
                return Some(Value::Object(out));
            }
        }

        *dropped
            .entry("invalid_image_url_block".to_string())
            .or_insert(0) += 1;
        return None;
    }

    if block_type == "tool_use" {
        let tool_use_id = obj.get("id").and_then(|v| v.as_str());
        let name = obj.get("name").and_then(|v| v.as_str());
        let tool_input = obj.get("input");

        if tool_use_id.is_none() || tool_use_id.unwrap().is_empty() {
            *dropped
                .entry("invalid_tool_use_id".to_string())
                .or_insert(0) += 1;
            return None;
        }
        if name.is_none() || name.unwrap().is_empty() {
            *dropped
                .entry("invalid_tool_use_name".to_string())
                .or_insert(0) += 1;
            return None;
        }

        let mut final_input = Map::new();
        if let Some(Value::Object(map)) = tool_input {
            if dict_nesting_depth(&Value::Object(map.clone()), 0) > MAX_TOOL_INPUT_DEPTH {
                *dropped
                    .entry("oversized_tool_input_depth".to_string())
                    .or_insert(0) += 1;
                return None;
            }
            final_input = map.clone();
        }

        let mut out = Map::new();
        out.insert("type".to_string(), Value::String("tool_use".to_string()));
        out.insert(
            "id".to_string(),
            Value::String(tool_use_id.unwrap().to_string()),
        );
        out.insert("name".to_string(), Value::String(name.unwrap().to_string()));
        out.insert("input".to_string(), Value::Object(final_input));
        return Some(Value::Object(out));
    }

    if block_type == "tool_result" {
        let tool_use_id = obj.get("tool_use_id").and_then(|v| v.as_str());
        if tool_use_id.is_none() || tool_use_id.unwrap().is_empty() {
            *dropped
                .entry("invalid_tool_result_id".to_string())
                .or_insert(0) += 1;
            return None;
        }

        let content = obj.get("content");
        let final_content;

        if let Some(Value::Array(arr)) = content {
            let mut normalized_content = Vec::new();
            for sub_item in arr {
                if let Some(block) = sanitize_content_block(
                    sub_item,
                    dropped,
                    supported_types,
                    allow_server_web_search_blocks,
                ) {
                    normalized_content.push(block);
                }
            }
            if normalized_content.is_empty() {
                *dropped
                    .entry("empty_tool_result_content".to_string())
                    .or_insert(0) += 1;
                return None;
            }
            final_content = Value::Array(normalized_content);
        } else if let Some(Value::String(s)) = content {
            if s.trim().is_empty() {
                *dropped
                    .entry("blank_tool_result_content".to_string())
                    .or_insert(0) += 1;
                return None;
            }
            final_content = Value::String(s.clone());
        } else {
            *dropped
                .entry("invalid_tool_result_content".to_string())
                .or_insert(0) += 1;
            return None;
        }

        let mut out = Map::new();
        out.insert("type".to_string(), Value::String("tool_result".to_string()));
        out.insert(
            "tool_use_id".to_string(),
            Value::String(tool_use_id.unwrap().to_string()),
        );
        out.insert("content".to_string(), final_content);
        return Some(Value::Object(out));
    }

    if block_type == "server_tool_use" {
        let tool_use_id = obj.get("id").and_then(|v| v.as_str());
        let name = obj.get("name").and_then(|v| v.as_str());
        let tool_input = obj.get("input");

        if tool_use_id.is_none() || tool_use_id.unwrap().is_empty() {
            *dropped
                .entry("invalid_server_tool_use_id".to_string())
                .or_insert(0) += 1;
            return None;
        }
        if name.is_none() || name.unwrap().is_empty() {
            *dropped
                .entry("invalid_server_tool_use_name".to_string())
                .or_insert(0) += 1;
            return None;
        }

        let mut final_input = Map::new();
        if let Some(Value::Object(map)) = tool_input {
            if dict_nesting_depth(&Value::Object(map.clone()), 0) > MAX_TOOL_INPUT_DEPTH {
                *dropped
                    .entry("oversized_server_tool_input_depth".to_string())
                    .or_insert(0) += 1;
                return None;
            }
            final_input = map.clone();
        } else if tool_input.is_some() {
            *dropped
                .entry("invalid_server_tool_use_input".to_string())
                .or_insert(0) += 1;
            return None;
        }

        let mut out = Map::new();
        out.insert(
            "type".to_string(),
            Value::String("server_tool_use".to_string()),
        );
        out.insert(
            "id".to_string(),
            Value::String(tool_use_id.unwrap().to_string()),
        );
        out.insert("name".to_string(), Value::String(name.unwrap().to_string()));
        out.insert("input".to_string(), Value::Object(final_input));
        return Some(Value::Object(out));
    }

    if block_type == "web_search_tool_result" {
        let tool_use_id = obj.get("tool_use_id").and_then(|v| v.as_str());
        if tool_use_id.is_none() || tool_use_id.unwrap().is_empty() {
            *dropped
                .entry("invalid_web_search_tool_result_id".to_string())
                .or_insert(0) += 1;
            return None;
        }

        let raw_content = obj.get("content");
        if let Some(Value::Array(arr)) = raw_content {
            let mut content_list = Vec::new();
            for entry in arr {
                if entry.is_object() {
                    content_list.push(entry.clone());
                }
            }
            if content_list.is_empty() {
                *dropped
                    .entry("invalid_web_search_tool_result_content".to_string())
                    .or_insert(0) += 1;
                return None;
            }
            let mut out = Map::new();
            out.insert(
                "type".to_string(),
                Value::String("web_search_tool_result".to_string()),
            );
            out.insert(
                "tool_use_id".to_string(),
                Value::String(tool_use_id.unwrap().to_string()),
            );
            out.insert("content".to_string(), Value::Array(content_list));
            return Some(Value::Object(out));
        }

        if let Some(Value::Object(o)) = raw_content {
            let mut out = Map::new();
            out.insert(
                "type".to_string(),
                Value::String("web_search_tool_result".to_string()),
            );
            out.insert(
                "tool_use_id".to_string(),
                Value::String(tool_use_id.unwrap().to_string()),
            );
            out.insert("content".to_string(), Value::Object(o.clone()));
            return Some(Value::Object(out));
        }

        *dropped
            .entry("invalid_web_search_tool_result_content".to_string())
            .or_insert(0) += 1;
        return None;
    }

    None
}

pub fn normalize_messages(
    messages: &Value,
    dropped: &mut HashMap<String, usize>,
    supported_types: &HashSet<&'static str>,
    allow_server_web_search_blocks: bool,
) -> Vec<Value> {
    if let Value::Array(arr) = messages {
        let _iter = arr.iter();
        let mut msg_arr = arr.clone();
        if arr.len() > MAX_MESSAGES {
            *dropped.entry("truncated_messages".to_string()).or_insert(0) += 1;
            msg_arr = arr[arr.len() - MAX_MESSAGES..].to_vec();
        }

        let mut normalized = Vec::new();
        for msg in msg_arr {
            if let Value::Object(msg_obj) = msg {
                let role = msg_obj.get("role").and_then(|v| v.as_str());
                if role != Some("user") && role != Some("assistant") {
                    let r = role.unwrap_or("None");
                    let key = format!("invalid_role:{}", r);
                    *dropped.entry(key).or_insert(0) += 1;
                    continue;
                }

                let content = msg_obj.get("content");
                if let Some(Value::String(s)) = content {
                    if s.trim().is_empty() {
                        *dropped
                            .entry("blank_string_message".to_string())
                            .or_insert(0) += 1;
                        continue;
                    }
                    let mut out = Map::new();
                    out.insert("role".to_string(), Value::String(role.unwrap().to_string()));
                    out.insert("content".to_string(), Value::String(s.clone()));
                    normalized.push(Value::Object(out));
                    continue;
                }

                if let Some(Value::Array(c_arr)) = content {
                    let mut blocks = Vec::new();
                    for item in c_arr {
                        if let Some(block) = sanitize_content_block(
                            item,
                            dropped,
                            supported_types,
                            allow_server_web_search_blocks,
                        ) {
                            blocks.push(block);
                        }
                    }
                    if blocks.is_empty() {
                        *dropped
                            .entry("empty_message_after_sanitize".to_string())
                            .or_insert(0) += 1;
                        continue;
                    }
                    let mut out = Map::new();
                    out.insert("role".to_string(), Value::String(role.unwrap().to_string()));
                    out.insert("content".to_string(), Value::Array(blocks));
                    normalized.push(Value::Object(out));
                    continue;
                }

                *dropped
                    .entry("invalid_message_content".to_string())
                    .or_insert(0) += 1;
            } else {
                *dropped.entry("non_dict_message".to_string()).or_insert(0) += 1;
            }
        }
        normalized
    } else {
        *dropped.entry("invalid_messages".to_string()).or_insert(0) += 1;
        Vec::new()
    }
}

pub fn sanitize_tools(
    tools: &Value,
    dropped: &mut HashMap<String, usize>,
    enable_web_search_tool: bool,
    normalize_web_search_as_client_tool: bool,
) -> Vec<Value> {
    if let Value::Array(arr) = tools {
        let mut cleaned_tools = Vec::new();
        for tool in arr {
            if let Value::Object(tool_obj) = tool {
                let tool_type = tool_obj.get("type").and_then(|v| v.as_str());
                if enable_web_search_tool
                    && tool_type.is_some()
                    && (get_web_search_allowed_tool_types().contains(tool_type.unwrap())
                        || tool_type.unwrap().starts_with(WEB_SEARCH_TOOL_TYPE_PREFIX))
                {
                    if normalize_web_search_as_client_tool {
                        let mut t = Map::new();
                        t.insert("name".to_string(), Value::String("web_search".to_string()));
                        t.insert(
                            "description".to_string(),
                            Value::String(
                                "Search the web and return relevant results with URLs.".to_string(),
                            ),
                        );

                        let mut schema = Map::new();
                        schema.insert("type".to_string(), Value::String("object".to_string()));
                        let mut props = Map::new();
                        let mut query_prop = Map::new();
                        query_prop.insert("type".to_string(), Value::String("string".to_string()));
                        props.insert("query".to_string(), Value::Object(query_prop));
                        schema.insert("properties".to_string(), Value::Object(props));
                        schema.insert(
                            "required".to_string(),
                            Value::Array(vec![Value::String("query".to_string())]),
                        );
                        t.insert("input_schema".to_string(), Value::Object(schema));

                        cleaned_tools.push(Value::Object(t));
                    } else {
                        cleaned_tools.push(tool.clone());
                    }
                    continue;
                }

                let mut source = tool_obj;
                if let Some(Value::Object(custom_obj)) = tool_obj.get("custom") {
                    source = custom_obj;
                }

                let mut name = source.get("name").and_then(|v| v.as_str());
                if name.is_none() {
                    name = tool_obj.get("name").and_then(|v| v.as_str());
                }

                let mut description = source.get("description").and_then(|v| v.as_str());
                if description.is_none() {
                    description = tool_obj.get("description").and_then(|v| v.as_str());
                }

                let mut input_schema = source.get("input_schema");
                if input_schema.is_none() || !input_schema.unwrap().is_object() {
                    input_schema = tool_obj.get("input_schema");
                }

                if name.is_none() || name.unwrap().is_empty() {
                    *dropped.entry("invalid_tool_name".to_string()).or_insert(0) += 1;
                    continue;
                }

                let schema_val = if let Some(schema) = input_schema {
                    if schema.is_object() {
                        schema.clone()
                    } else {
                        *dropped
                            .entry("tool_schema_defaulted".to_string())
                            .or_insert(0) += 1;
                        let mut s = Map::new();
                        s.insert("type".to_string(), Value::String("object".to_string()));
                        s.insert("properties".to_string(), Value::Object(Map::new()));
                        Value::Object(s)
                    }
                } else {
                    *dropped
                        .entry("tool_schema_defaulted".to_string())
                        .or_insert(0) += 1;
                    let mut s = Map::new();
                    s.insert("type".to_string(), Value::String("object".to_string()));
                    s.insert("properties".to_string(), Value::Object(Map::new()));
                    Value::Object(s)
                };

                let mut out_tool = Map::new();
                out_tool.insert("name".to_string(), Value::String(name.unwrap().to_string()));
                out_tool.insert("input_schema".to_string(), schema_val);

                if let Some(desc) = description {
                    if !desc.is_empty() {
                        let mut d = desc.to_string();
                        if d.len() > MAX_TOOL_DESC_LEN {
                            d.truncate(MAX_TOOL_DESC_LEN);
                        }
                        out_tool.insert("description".to_string(), Value::String(d));
                    }
                }
                cleaned_tools.push(Value::Object(out_tool));
            } else {
                *dropped.entry("non_dict_tool".to_string()).or_insert(0) += 1;
            }
        }
        cleaned_tools
    } else {
        *dropped.entry("invalid_tools".to_string()).or_insert(0) += 1;
        Vec::new()
    }
}

pub fn sanitize_thinking(thinking: &Value, dropped: &mut HashMap<String, usize>) -> Option<Value> {
    if let Value::Object(obj) = thinking {
        let mut out = Map::new();
        if let Some(Value::String(t)) = obj.get("type") {
            out.insert("type".to_string(), Value::String(t.clone()));
        }
        if let Some(Value::Number(n)) = obj.get("budget_tokens") {
            if n.is_i64() || n.is_u64() {
                out.insert("budget_tokens".to_string(), Value::Number(n.clone()));
            }
        }
        if out.is_empty() {
            None
        } else {
            Some(Value::Object(out))
        }
    } else {
        *dropped.entry("invalid_thinking".to_string()).or_insert(0) += 1;
        None
    }
}

pub fn sanitize_output_config(
    output_config: &Value,
    dropped: &mut HashMap<String, usize>,
) -> Option<Value> {
    if let Value::Object(obj) = output_config {
        if let Some(Value::String(effort)) = obj.get("effort") {
            if !effort.is_empty() {
                let mut out = Map::new();
                out.insert("effort".to_string(), Value::String(effort.clone()));
                return Some(Value::Object(out));
            }
        }
        *dropped
            .entry("output_config_dropped".to_string())
            .or_insert(0) += 1;
        None
    } else {
        *dropped
            .entry("invalid_output_config".to_string())
            .or_insert(0) += 1;
        None
    }
}

pub fn metadata_summary(metadata_value: &Value) -> String {
    match metadata_value {
        Value::Object(map) => {
            let mut keys: Vec<String> = map.keys().cloned().collect();
            keys.sort();
            let preview = if keys.len() > 20 {
                format!("{:?}", &keys[..20])
            } else {
                format!("{:?}", keys)
            };
            format!(
                "type=dict keys={} keys_count={} size_hint=top_level_items:{}",
                preview,
                keys.len(),
                map.len()
            )
        }
        Value::Array(arr) => format!("type=list size_hint=top_level_items:{}", arr.len()),
        Value::String(s) => format!("type=str size_hint=chars:{}", s.len()),
        Value::Null => "type=null size_hint=n/a".to_string(),
        Value::Bool(_) => "type=bool size_hint=n/a".to_string(),
        Value::Number(_) => "type=number size_hint=n/a".to_string(),
    }
}

pub fn looks_like_connection_probe(raw_body: &Map<String, Value>) -> bool {
    if let Some(Value::Bool(b)) = raw_body.get("stream") {
        if *b {
            return false;
        }
    }

    if let Some(Value::Number(n)) = raw_body.get("max_tokens") {
        if let Some(val) = n.as_i64() {
            if val <= 0 || val > 1 {
                return false;
            }
        } else {
            return false;
        }
    } else {
        return false;
    }

    let checks = [
        "system",
        "tools",
        "metadata",
        "thinking",
        "output_config",
        "tool_choice",
    ];
    for check in checks.iter() {
        if raw_body.contains_key(*check) {
            return false;
        }
    }

    if let Some(Value::Array(arr)) = raw_body.get("messages") {
        if arr.len() != 1 {
            return false;
        }
        if let Value::Object(msg) = &arr[0] {
            if msg.get("role").and_then(|v| v.as_str()) != Some("user") {
                return false;
            }
            let content = msg.get("content");
            if let Some(Value::String(s)) = content {
                let len = s.trim().len();
                return len > 0 && len <= 4;
            }
            if let Some(Value::Array(c_arr)) = content {
                if c_arr.len() == 1 {
                    if let Value::Object(block) = &c_arr[0] {
                        if block.get("type").and_then(|v| v.as_str()) == Some("text") {
                            if let Some(Value::String(s)) = block.get("text") {
                                let len = s.trim().len();
                                return len > 0 && len <= 4;
                            }
                        }
                    }
                }
            }
        }
    }
    false
}

pub struct SanitizeResult {
    pub sanitized_body: Map<String, Value>,
    pub dropped_stats: HashMap<String, usize>,
    pub removed_fields: Vec<String>,
}

pub fn sanitize_request_body(
    raw_body: &Map<String, Value>,
    image_support: bool,
    passthrough_metadata: bool,
    enable_web_search_tool: bool,
    normalize_web_search_as_client_tool: bool,
    default_max_tokens: i64,
    min_compat_max_tokens: i64,
) -> SanitizeResult {
    let mut dropped = HashMap::new();
    let mut removed_fields = Vec::new();
    let mut sanitized = Map::new();

    let raw_max_tokens = raw_body.get("max_tokens");
    let is_probe = looks_like_connection_probe(raw_body);
    let probe_kind = if is_probe {
        "connection_test"
    } else {
        "normal"
    };
    let allow_server_web_search_blocks =
        enable_web_search_tool && !normalize_web_search_as_client_tool;
    let supported_types =
        get_supported_content_types(image_support, allow_server_web_search_blocks);

    let metadata_present = raw_body.contains_key("metadata");
    let mode = if passthrough_metadata {
        "passthrough_enabled"
    } else {
        "removed"
    };
    if metadata_present {
        let summary = metadata_summary(raw_body.get("metadata").unwrap());
        println!("[gateway metadata] present=yes mode={} {}", mode, summary);
    } else {
        println!("[gateway metadata] present=no mode={}", mode);
    }

    let allowlist = get_default_top_level_allowlist();
    for (key, value) in raw_body {
        if allowlist.contains(key.as_str()) || (key == "metadata" && passthrough_metadata) {
            sanitized.insert(key.clone(), value.clone());
        } else {
            removed_fields.push(key.clone());
        }
    }

    if let Some(messages) = sanitized.get("messages") {
        let normalized = normalize_messages(
            messages,
            &mut dropped,
            &supported_types,
            allow_server_web_search_blocks,
        );
        sanitized.insert("messages".to_string(), Value::Array(normalized));
    }

    if let Some(system) = sanitized.get("system") {
        sanitized.insert("system".to_string(), normalize_system(system));
    }

    if let Some(tools) = sanitized.get("tools") {
        let normalized = sanitize_tools(
            tools,
            &mut dropped,
            enable_web_search_tool,
            normalize_web_search_as_client_tool,
        );
        if !normalized.is_empty() {
            sanitized.insert("tools".to_string(), Value::Array(normalized));
        } else {
            sanitized.remove("tools");
        }
    }

    if let Some(Value::Object(tool_choice)) = sanitized.get("tool_choice") {
        if tool_choice.contains_key("disable_parallel_tool_use") {
            println!("[gateway compat] forwarding tool_choice.disable_parallel_tool_use as-is; upstream may ignore this field");
        }
    }

    if let Some(thinking) = sanitized.get("thinking") {
        if let Some(normalized) = sanitize_thinking(thinking, &mut dropped) {
            sanitized.insert("thinking".to_string(), normalized);
        } else {
            sanitized.remove("thinking");
        }
    }

    if let Some(output_config) = sanitized.get("output_config") {
        if let Some(normalized) = sanitize_output_config(output_config, &mut dropped) {
            sanitized.insert("output_config".to_string(), normalized);
        } else {
            sanitized.remove("output_config");
        }
    }

    let max_tokens = sanitized.get("max_tokens").and_then(|v| v.as_i64());
    if max_tokens.is_none_or(|v| v <= 0) {
        sanitized.insert(
            "max_tokens".to_string(),
            Value::Number(serde_json::Number::from(default_max_tokens)),
        );
        *dropped
            .entry("max_tokens_defaulted".to_string())
            .or_insert(0) += 1;
    } else if is_probe && max_tokens.unwrap() < min_compat_max_tokens {
        sanitized.insert(
            "max_tokens".to_string(),
            Value::Number(serde_json::Number::from(min_compat_max_tokens)),
        );
        *dropped
            .entry("max_tokens_raised_for_compat".to_string())
            .or_insert(0) += 1;
    }

    let raw_mt_str = match raw_max_tokens {
        Some(v) => v.to_string(),
        None => "None".to_string(),
    };

    let eff_mt = match sanitized.get("max_tokens") {
        Some(v) => v.to_string(),
        None => "None".to_string(),
    };

    println!(
        "[gateway compat] probe_kind={} raw_max_tokens={} effective_max_tokens={}",
        probe_kind, raw_mt_str, eff_mt
    );

    SanitizeResult {
        sanitized_body: sanitized,
        dropped_stats: dropped,
        removed_fields,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_normalize_system_string() {
        let input = json!("You are a helpful assistant.");
        let result = normalize_system(&input);
        assert_eq!(result, json!("You are a helpful assistant."));
    }

    #[test]
    fn test_normalize_system_array() {
        let input = json!(["Part 1.", "Part 2."]);
        let result = normalize_system(&input);
        let expected = json!([
            {"type": "text", "text": "Part 1."},
            {"type": "text", "text": "Part 2."}
        ]);
        assert_eq!(result, expected);
    }

    #[test]
    fn test_normalize_system_empty_array() {
        let input = json!([]);
        let result = normalize_system(&input);
        assert_eq!(result, json!([]));
    }

    #[test]
    fn test_looks_like_connection_probe() {
        let probe = json!({
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "a"}]
        });
        assert!(looks_like_connection_probe(probe.as_object().unwrap()));

        let not_probe_tokens = json!({
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "a"}]
        });
        assert!(!looks_like_connection_probe(
            not_probe_tokens.as_object().unwrap()
        ));

        let not_probe_system = json!({
            "max_tokens": 1,
            "system": "Hello",
            "messages": [{"role": "user", "content": "a"}]
        });
        assert!(!looks_like_connection_probe(
            not_probe_system.as_object().unwrap()
        ));
    }

    #[test]
    fn test_sanitize_output_config() {
        let mut dropped = HashMap::new();

        let valid = json!({"effort": "high"});
        let result = sanitize_output_config(&valid, &mut dropped);
        assert_eq!(result, Some(json!({"effort": "high"})));
        assert!(dropped.is_empty());

        let invalid = json!({"other": "field"});
        let result = sanitize_output_config(&invalid, &mut dropped);
        assert_eq!(result, None);
        assert_eq!(dropped.get("output_config_dropped"), Some(&1));
    }
}
