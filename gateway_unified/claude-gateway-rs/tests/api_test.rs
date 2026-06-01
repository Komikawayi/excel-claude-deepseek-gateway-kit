use reqwest::Client;
use std::process::{Child, Command};
use std::time::Duration;

struct TestServer {
    child: Child,
    port: u16,
}

impl TestServer {
    fn start(port: u16) -> Self {
        let build_status = Command::new("cargo")
            .arg("build")
            .status()
            .expect("Failed to build before tests");
        assert!(build_status.success(), "cargo build failed");

        let child = Command::new("cargo")
            .arg("run")
            .env("GATEWAY_PORT", port.to_string())
            .env("GATEWAY_HOST", "127.0.0.1")
            .env("ACTIVE_PROVIDER", "auto")
            .spawn()
            .expect("Failed to start gateway server");

        TestServer { child, port }
    }

    fn url(&self, path: &str) -> String {
        format!("http://127.0.0.1:{}{}", self.port, path)
    }

    async fn wait_ready(&self) {
        let client = Client::new();
        for i in 0..30 {
            if client
                .get(&self.url("/healthz"))
                .timeout(Duration::from_secs(1))
                .send()
                .await
                .is_ok()
            {
                return;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
            if i == 29 {
                panic!("Server did not start within 15 seconds");
            }
        }
    }
}

impl Drop for TestServer {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[tokio::test]
async fn test_healthz_endpoint() {
    let server = TestServer::start(18801);
    server.wait_ready().await;
    let client = Client::new();

    let res = client
        .get(&server.url("/healthz"))
        .send()
        .await
        .expect("Failed to execute request");

    assert_eq!(res.status(), 200);

    let body: serde_json::Value = res.json().await.expect("Failed to parse JSON");
    assert_eq!(body.get("status").and_then(|v| v.as_str()), Some("ok"));
}

#[tokio::test]
async fn test_fallback_endpoint() {
    let server = TestServer::start(18802);
    server.wait_ready().await;
    let client = Client::new();

    let res = client
        .get(&server.url("/nonexistent"))
        .send()
        .await
        .expect("Failed to execute request");

    assert_eq!(res.status(), 404);
}

#[tokio::test]
async fn test_models_endpoint() {
    let server = TestServer::start(18803);
    server.wait_ready().await;
    let client = Client::new();

    let res = client
        .get(&server.url("/v1/models"))
        .send()
        .await
        .expect("Failed to execute request");

    assert_eq!(res.status(), 200);

    let body: serde_json::Value = res.json().await.expect("Failed to parse JSON");
    assert!(
        body.get("data").is_some(),
        "models response should have 'data' field"
    );
}
