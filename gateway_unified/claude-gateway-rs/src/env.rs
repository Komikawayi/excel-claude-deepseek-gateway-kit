use std::env;

pub fn env_int(name: &str, default: i64) -> i64 {
    env::var(name)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .and_then(|s| s.parse().ok())
        .map(|v: i64| if v > 0 { v } else { default })
        .unwrap_or(default)
}

pub fn env_bool(name: &str, default: bool) -> bool {
    env::var(name)
        .ok()
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .map(|s| matches!(s.as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(default)
}

pub fn env_float(name: &str, default: f64) -> f64 {
    env::var(name)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .and_then(|s| s.parse().ok())
        .map(|v: f64| if v > 0.0 { v } else { default })
        .unwrap_or(default)
}

pub fn env_str(name: &str, default: &str) -> String {
    env::var(name)
        .unwrap_or_else(|_| default.to_string())
        .trim()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_env_int_default() {
        assert_eq!(env_int("NONEXISTENT_KEY_12345", 42), 42);
    }

    #[test]
    fn test_env_bool_default() {
        assert_eq!(env_bool("NONEXISTENT_KEY_12345", true), true);
    }

    #[test]
    fn test_env_str_default() {
        assert_eq!(env_str("NONEXISTENT_KEY_12345", "fallback"), "fallback");
    }
}
