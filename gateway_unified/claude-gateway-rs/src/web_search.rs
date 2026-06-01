#![allow(dead_code)]

use anyhow::Result;
use regex::Regex;
use std::time::Duration;
use url::Url;

lazy_static::lazy_static! {
    static ref RESULT_ANCHOR_RE: Regex = Regex::new(r#"(?is)<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>"#).unwrap();
    static ref RESULT_SNIPPET_RE: Regex = Regex::new(r#"(?is)class="result__snippet"[^>]*>(.*?)</(?:a|div)>"#).unwrap();
    static ref TAG_RE: Regex = Regex::new(r#"<[^>]+>"#).unwrap();
}

fn strip_tags(raw: &str) -> String {
    let text = TAG_RE.replace_all(raw, "");
    html_escape::decode_html_entities(&text).trim().to_string()
}

fn normalize_url(raw_href: &str) -> String {
    let mut value = html_escape::decode_html_entities(raw_href.trim()).into_owned();
    if value.is_empty() {
        return String::new();
    }

    if value.starts_with("//") {
        value = format!("https:{}", value);
    } else if value.starts_with("/l/?") {
        value = format!("https://duckduckgo.com{}", value);
    }

    if let Ok(parsed) = Url::parse(&value) {
        if parsed.host_str() == Some("duckduckgo.com") && parsed.path() == "/l/" {
            for (k, v) in parsed.query_pairs() {
                if k == "uddg" {
                    return urlencoding::decode(&v)
                        .map(|s| s.trim().to_string())
                        .unwrap_or_else(|_| v.trim().to_string());
                }
            }
        }
    }
    value
}

fn domain_allowed(url: &str, allowed_domains: Option<&[String]>) -> bool {
    let allowed = match allowed_domains {
        Some(domains) => domains,
        None => return true,
    };
    if allowed.is_empty() {
        return true;
    }

    if let Ok(parsed) = Url::parse(url) {
        if let Some(netloc) = parsed.host_str() {
            let netloc = netloc.to_lowercase();
            for domain in allowed {
                let d = domain.trim().to_lowercase();
                if d.is_empty() {
                    continue;
                }
                if netloc == d || netloc.ends_with(&format!(".{}", d)) {
                    return true;
                }
            }
        }
    }
    false
}

pub struct SearchResult {
    pub title: String,
    pub url: String,
    pub snippet: String,
}

async fn search_duckduckgo_html_once(
    query: &str,
    max_results: usize,
    timeout_s: f64,
    allowed_domains: Option<&[String]>,
) -> Result<Vec<SearchResult>> {
    let q = query.trim();
    if q.is_empty() {
        return Ok(Vec::new());
    }

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs_f64(timeout_s))
        .redirect(reqwest::redirect::Policy::limited(10))
        .build()?;

    let resp = client.get("https://html.duckduckgo.com/html/")
        .query(&[("q", q)])
        .header("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")
        .header("accept-language", "zh-CN,zh;q=0.9,en;q=0.8")
        .send()
        .await?;

    if !resp.status().is_success() {
        return Ok(Vec::new());
    }

    let html_text = resp.text().await?;
    let mut results = Vec::new();

    for cap in RESULT_ANCHOR_RE.captures_iter(&html_text) {
        let href = normalize_url(cap.get(1).map(|m| m.as_str()).unwrap_or(""));
        if href.is_empty() || !domain_allowed(&href, allowed_domains) {
            continue;
        }

        let title = strip_tags(cap.get(2).map(|m| m.as_str()).unwrap_or(""));
        if title.is_empty() {
            continue;
        }

        let m = cap.get(0).unwrap();
        let end_idx = m.end();
        let tail_end = std::cmp::min(end_idx + 2200, html_text.len());
        let tail = &html_text[end_idx..tail_end];

        let mut snippet = String::new();
        if let Some(snippet_cap) = RESULT_SNIPPET_RE.captures(tail) {
            snippet = strip_tags(snippet_cap.get(1).map(|m| m.as_str()).unwrap_or(""));
        }

        results.push(SearchResult {
            title,
            url: href,
            snippet,
        });

        if results.len() >= max_results {
            break;
        }
    }

    Ok(results)
}

pub async fn search_duckduckgo_html(
    query: &str,
    max_results: usize,
    timeout_s: f64,
    allowed_domains: Option<&[String]>,
    retries: usize,
) -> Result<Vec<SearchResult>> {
    let mut last_err = None;
    let total_attempts = std::cmp::max(1, retries + 1);

    for attempt in 0..total_attempts {
        match search_duckduckgo_html_once(query, max_results, timeout_s, allowed_domains).await {
            Ok(res) => return Ok(res),
            Err(e) => {
                println!(
                    "[gateway web_search] retryable_error attempt={}/{} type={}",
                    attempt + 1,
                    total_attempts,
                    e
                );
                last_err = Some(e);
                if attempt >= total_attempts - 1 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(350)).await;
            }
        }
    }

    if let Some(e) = last_err {
        return Err(e);
    }
    Ok(Vec::new())
}

pub fn format_web_search_tool_result_text(query: &str, results: &[SearchResult]) -> String {
    let q = query.trim();
    let q_display = if q.is_empty() { "(empty)" } else { q };
    let mut lines = vec![format!("query: {}", q_display)];

    if results.is_empty() {
        lines.push("no results found".to_string());
        return lines.join("\n");
    }

    for (idx, item) in results.iter().enumerate() {
        let title = if item.title.trim().is_empty() {
            "(untitled)"
        } else {
            item.title.trim()
        };
        lines.push(format!("{}. {}", idx + 1, title));
        if !item.url.is_empty() {
            lines.push(format!("url: {}", item.url.trim()));
        }
        if !item.snippet.is_empty() {
            lines.push(format!("snippet: {}", item.snippet.trim()));
        }
    }

    lines.join("\n")
}
