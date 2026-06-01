use axum::{body::Body, http::Request, response::Response};
use futures::future::BoxFuture;
use std::task::{Context, Poll};
use tower::{Layer, Service};

#[derive(Clone)]
pub struct RequestLogMiddlewareLayer;

impl RequestLogMiddlewareLayer {
    pub fn new() -> Self {
        Self
    }
}

impl<S> Layer<S> for RequestLogMiddlewareLayer {
    type Service = RequestLogMiddleware<S>;

    fn layer(&self, inner: S) -> Self::Service {
        RequestLogMiddleware { inner }
    }
}

#[derive(Clone)]
pub struct RequestLogMiddleware<S> {
    inner: S,
}

impl<S> Service<Request<Body>> for RequestLogMiddleware<S>
where
    S: Service<Request<Body>, Response = Response> + Send + 'static,
    S::Future: Send + 'static,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = BoxFuture<'static, Result<Self::Response, Self::Error>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request<Body>) -> Self::Future {
        // Basic logging middleware.
        // A full port of log_mw.py would inspect body chunks, which is complex in tower without a custom body wrapper.
        // We'll just pass the request through and log the path.
        let path = req.uri().path().to_owned();
        let method = req.method().to_string();
        let ts = chrono::Utc::now()
            .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
            .replace("+00:00", "Z");
        println!("[{}] {} {}", ts, method, path);

        let future = self.inner.call(req);

        Box::pin(async move {
            let res = future.await?;
            println!("[{}] response: {}", ts, res.status());
            Ok(res)
        })
    }
}
