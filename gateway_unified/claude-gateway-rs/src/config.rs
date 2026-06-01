use std::env;
use std::fs;
use std::path::PathBuf;

/// 获取配置文件路径（优先当前目录 .env，其次 %APPDATA%）
pub fn config_path() -> PathBuf {
    let local = PathBuf::from(".env");
    if local.exists() {
        return local;
    }
    if let Ok(appdata) = env::var("APPDATA") {
        let dir = PathBuf::from(appdata).join("claude-gateway");
        return dir.join(".env");
    }
    local
}

/// 首次运行检测：无 .env 时自动生成模板并提示
pub fn ensure_config() -> anyhow::Result<PathBuf> {
    let path = config_path();
    if path.exists() {
        return Ok(path);
    }

    // 生成模板
    let template = include_str!("../.env.example");
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&path, template)?;

    eprintln!("========================================");
    eprintln!("  首次运行 - 已生成配置文件");
    eprintln!("  路径: {}", path.display());
    eprintln!("  请编辑配置文件填入 API Key 后重启");
    eprintln!("========================================");

    Ok(path)
}

/// 解析命令行参数覆盖 .env 配置
pub fn apply_cli_overrides() {
    let args: Vec<String> = env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--port" | "-p" => {
                if let Some(val) = args.get(i + 1) {
                    env::set_var("GATEWAY_PORT", val);
                    i += 2;
                } else {
                    i += 1;
                }
            }
            "--provider" => {
                if let Some(val) = args.get(i + 1) {
                    env::set_var("ACTIVE_PROVIDER", val);
                    i += 2;
                } else {
                    i += 1;
                }
            }
            "--key" | "-k" => {
                if let Some(val) = args.get(i + 1) {
                    // 根据 provider 设置对应 key
                    let provider = env::var("ACTIVE_PROVIDER").unwrap_or_default();
                    let env_key = match provider.to_lowercase().as_str() {
                        "deepseek" => "DEEPSEEK_API_KEY",
                        "kimi" => "KIMI_API_KEY",
                        "mimo" => "MIMO_API_KEY",
                        "minimax" => "MINIMAX_API_KEY",
                        _ => "DEEPSEEK_API_KEY",
                    };
                    env::set_var(env_key, val);
                    i += 2;
                } else {
                    i += 1;
                }
            }
            "--host" | "-h" => {
                if let Some(val) = args.get(i + 1) {
                    env::set_var("GATEWAY_HOST", val);
                    i += 2;
                } else {
                    i += 1;
                }
            }
            "--help" => {
                print_help();
                std::process::exit(0);
            }
            _ => {
                i += 1;
            }
        }
    }
}

fn print_help() {
    eprintln!(
        r#"claude-gateway-rs - Claude 兼容 API 网关

用法: claude-gateway-rs [选项]

选项:
  -p, --port <PORT>        监听端口 (默认: 8790)
  --host <HOST>            监听地址 (默认: 127.0.0.1)
  --provider <PROVIDER>    提供商: deepseek|kimi|mimo|minimax|auto
  -k, --key <KEY>          API Key (覆盖 .env 中的配置)
  --help                   显示帮助

配置文件:
  优先读取当前目录 .env，其次 %APPDATA%/claude-gateway/.env
  首次运行自动生成 .env.example 模板
"#
    );
}
