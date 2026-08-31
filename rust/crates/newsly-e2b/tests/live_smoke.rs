use std::collections::BTreeMap;
use std::env;
use std::time::Duration;

use newsly_e2b::{
    CommandRequest, ControlPlaneConfig, DirectE2bProvider, ExecutionTag, FileLimits, NetworkPolicy,
    OutputLimits, SandboxProvider, SandboxRequest, SandboxUser,
};
use secrecy::SecretString;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

#[tokio::test]
#[ignore = "requires E2B_API_KEY and creates one disposable live sandbox"]
async fn live_sandbox_can_stream_a_command_and_is_destroyed() {
    let api_key = env::var("E2B_API_KEY")
        .or_else(|_| env::var("LLM_TASK_SANDBOX_E2B_API_KEY"))
        .expect("E2B_API_KEY or LLM_TASK_SANDBOX_E2B_API_KEY is required");
    let template_id =
        env::var("NEWSLY_E2B_TEST_TEMPLATE_ID").unwrap_or_else(|_| "newsly-agent".to_owned());
    let provider = DirectE2bProvider::new(
        ControlPlaneConfig::production(SecretString::from(api_key))
            .expect("production E2B control-plane configuration must be valid"),
        FileLimits::default(),
    )
    .expect("E2B provider must build");

    let sandbox = provider
        .create_sandbox(&SandboxRequest {
            template_id,
            timeout: 120,
            auto_pause: true,
            auto_pause_memory: true,
            secure: true,
            allow_internet_access: false,
            metadata: BTreeMap::from([("feature".to_owned(), "newsly_e2b_live_smoke".to_owned())]),
            env_vars: BTreeMap::new(),
            network: Some(NetworkPolicy::deny_all()),
        })
        .await
        .unwrap_or_else(|error| panic!("sandbox creation failed: {error:#?}"));

    let run = async {
        let stream = provider
            .start_process(
                &sandbox,
                CommandRequest {
                    command: "/bin/printf".to_owned(),
                    args: vec!["newsly-e2b-live-smoke".to_owned()],
                    env: BTreeMap::new(),
                    cwd: None,
                    username: Some(SandboxUser::parse("user").expect("valid sandbox user")),
                    tag: ExecutionTag::new(),
                    stdin_enabled: false,
                    absolute_deadline: Instant::now() + Duration::from_secs(30),
                    idle_timeout: Duration::from_secs(15),
                    output_limits: OutputLimits::default(),
                },
                CancellationToken::new(),
            )
            .await?;
        stream.collect_result().await
    }
    .await;

    let cleanup = provider.kill_sandbox(&sandbox.sandbox_id).await;
    if let Err(error) = cleanup {
        panic!("sandbox cleanup failed after command result {run:#?}: {error:#?}");
    }
    let result = run.unwrap_or_else(|error| panic!("live command stream failed: {error:#?}"));
    assert_eq!(result.exit_code, 0);
    assert_eq!(result.output.stdout, "newsly-e2b-live-smoke");
    assert!(result.output.stderr.is_empty());
}
