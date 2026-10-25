//! One durable clock and private candidates survive fresh sandbox attempts.
use super::{
    AcquiredTaskSandbox, AgentOutcome, AgentRuntimeError, Arc, CancellationToken, Duration,
    HOUSE_DESIGN_APPENDIX, Instant, LearningDeckAgentError, LearningDeckAgentRunResult,
    LearningDeckAgentRuntime, LearningDeckArtifactError, LearningDeckArtifactStore,
    LearningDeckEvents, LearningDeckGenerationClaimFence, LearningDeckTaskSnapshot, Map,
    NewslyTranscript, SandboxHandle, TaskToolExecutor, Value, agent_network_policy,
    build_user_prompt, json,
};
use super::{
    candidate::{capture_candidate, restore_candidate},
    policy::GenerationBudget,
    validation::DeckValidator,
};
use crate::learning_deck::{
    artifacts::drafts::{DeckCandidate, digest},
    browser::hosted_viewer_html,
};
use newsly_db::{
    begin_learning_deck_generation, learning_deck_generation_is_owned, publish_learning_deck_draft,
    publish_learning_deck_progress,
};

struct Attempt<'a> {
    runtime: &'a LearningDeckAgentRuntime,
    task: &'a LearningDeckTaskSnapshot,
    fence: &'a LearningDeckGenerationClaimFence,
    artifacts: &'a LearningDeckArtifactStore,
    fingerprint: &'a str,
    budget: GenerationBudget,
    tools: TaskToolExecutor,
    sandbox: &'a SandboxHandle,
    cancellation: CancellationToken,
    events: Arc<LearningDeckEvents>,
    last_candidate: Option<DeckCandidate>,
    last_digest: Option<String>,
}

impl LearningDeckAgentRuntime {
    pub(in crate::learning_deck) async fn run(
        &self,
        task: &LearningDeckTaskSnapshot,
        source: &Map<String, Value>,
        fence: &LearningDeckGenerationClaimFence,
        artifacts: &LearningDeckArtifactStore,
        cancellation: CancellationToken,
    ) -> Result<LearningDeckAgentRunResult, LearningDeckAgentError> {
        let fingerprint =
            input_fingerprint(source, task.interests_prompt.as_deref(), &task.tool_policy)?;
        let started = Instant::now();
        let state = begin_learning_deck_generation(
            &self.pool,
            fence,
            &fingerprint,
            i64::try_from(self.config.generation_seconds).unwrap_or(900),
        )
        .await?
        .ok_or(LearningDeckAgentError::Cancelled)?;
        let remaining = Duration::from_secs(u64::try_from(state.remaining_seconds).unwrap_or(0));
        let budget = GenerationBudget::new(started, remaining);
        require_attempt_budget(remaining, state.draft_object_key.is_some())?;
        let deadline = budget.deadline - Duration::from_secs(60);
        let acquired = tokio::time::timeout_at(
            deadline,
            self.lifecycle.acquire_for_task(
                task.user_id,
                task.id,
                "learning_deck",
                deadline,
                cancellation.child_token(),
            ),
        )
        .await
        .map_err(|_| LearningDeckAgentError::Deadline)??;
        let events = Arc::new(LearningDeckEvents::new(self.pool.clone(), task));
        events.push("generation_budget",json!({"deadline_at":state.deadline_at,"remaining_seconds":state.remaining_seconds,"resuming":state.draft_object_key.is_some()}));
        let result = {
            let operation = self.run_acquired(
                task,
                source,
                fence,
                artifacts,
                &fingerprint,
                state.draft_object_key.as_deref(),
                &acquired,
                budget,
                cancellation.child_token(),
                events.clone(),
            );
            tokio::pin!(operation);
            let mut ownership = tokio::time::interval(Duration::from_secs(10));
            ownership.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    result=&mut operation=>break result,
                    ()=cancellation.cancelled()=>break Err(LearningDeckAgentError::Cancelled),
                    ()=tokio::time::sleep_until(deadline)=>break Err(LearningDeckAgentError::Deadline),
                    _=ownership.tick()=>match tokio::time::timeout(Duration::from_secs(5),learning_deck_generation_is_owned(&self.pool,fence)).await {
                        Ok(Ok(true))=>{}, Ok(Ok(false))=>break Err(LearningDeckAgentError::Cancelled),
                        Ok(Err(error))=>tracing::warn!(task_id=task.id,error=%error,"Learning Deck ownership probe failed; queue lease watcher remains active"),
                        Err(_)=>tracing::warn!(task_id=task.id,"Learning Deck ownership probe timed out; queue lease watcher remains active"),
                    }
                }
            }
        };
        cancellation.cancel();
        // Revoke network access before destruction, including failed and interrupted attempts.
        let _ = tokio::time::timeout_at(
            budget.deadline.min(Instant::now() + Duration::from_secs(5)),
            self.provider.reset_network(&acquired.sandbox.sandbox_id),
        )
        .await;
        let _ = tokio::time::timeout(Duration::from_secs(5), events.usage.finish()).await;
        let result = result.map_err(|source| LearningDeckAgentError::SandboxExecution {
            source: Box::new(source),
            sandbox_provider: "e2b".to_owned(),
            sandbox_id: acquired.sandbox.sandbox_id.as_str().to_owned(),
            events: events.values(),
        });
        match tokio::time::timeout_at(
            budget
                .deadline
                .min(Instant::now() + Duration::from_secs(10)),
            acquired.release(),
        )
        .await
        {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                tracing::error!(task_id=task.id,error=%error,"failed to destroy Learning Deck task sandbox");
            }
            Err(_) => tracing::warn!(
                task_id = task.id,
                "sandbox cleanup continues through durable reconciler"
            ),
        }
        result
    }

    #[allow(clippy::too_many_arguments)]
    async fn run_acquired(
        &self,
        task: &LearningDeckTaskSnapshot,
        source: &Map<String, Value>,
        fence: &LearningDeckGenerationClaimFence,
        artifacts: &LearningDeckArtifactStore,
        fingerprint: &str,
        draft_key: Option<&str>,
        acquired: &AcquiredTaskSandbox,
        budget: GenerationBudget,
        cancellation: CancellationToken,
        events: Arc<LearningDeckEvents>,
    ) -> Result<LearningDeckAgentRunResult, LearningDeckAgentError> {
        let deadline = budget.deadline - Duration::from_secs(60);
        let sandbox = &acquired.sandbox;
        self.prepare_workspace(sandbox, task, deadline, cancellation.child_token())
            .await?;
        let tools = TaskToolExecutor::new(
            self.provider.clone(),
            sandbox.clone(),
            &task.workspace_path,
            deadline,
            cancellation.child_token(),
            self.config.base.max_output_chars,
            self.exa.clone(),
            self.pool.clone(),
            task.user_id,
            self.body_store.clone(),
        )?;
        self.write_inputs(&tools, task, source).await?;
        let policy = agent_network_policy(
            self.config.base.public_base_url.as_ref(),
            true,
            "learning_deck",
        )
        .await;
        self.provider
            .update_network(&sandbox.sandbox_id, &policy)
            .await?;
        let mut attempt = Attempt {
            runtime: self,
            task,
            fence,
            artifacts,
            fingerprint,
            budget,
            tools,
            sandbox,
            cancellation,
            events,
            last_candidate: None,
            last_digest: None,
        };
        let mut outcome = empty_outcome(&self.config.model_spec.canonical());
        let mut validation = None;
        let mut interrupted = false;
        if let Some(key) = draft_key {
            attempt.progress("Resuming saved draft").await?;
            let restored = match artifacts
                .load_draft(task.user_id, task.deck_id, task.id, fingerprint, key)
                .await
            {
                Ok(candidate) => candidate,
                Err(
                    LearningDeckArtifactError::Contract(_)
                    | LearningDeckArtifactError::Json(_)
                    | LearningDeckArtifactError::Utf8(_)
                    | LearningDeckArtifactError::UnsafeAssetPath(_)
                    | LearningDeckArtifactError::SourceTooLarge(_),
                ) => {
                    attempt.events.push(
                        "draft_rejected",
                        json!({"reason":"invalid private checkpoint"}),
                    );
                    None
                }
                Err(error) => return Err(error.into()),
            };
            if let Some(candidate) = restored {
                attempt.last_digest = Some(candidate_digest(&candidate)?);
                attempt.last_candidate = Some(candidate.clone());
                restore_candidate(&attempt.tools, &self.config.limits, &candidate).await?;
                validation = Some(attempt.validate(&candidate).await);
            }
        }
        if validation.is_none() {
            require_attempt_budget(GenerationBudget::remaining(budget.deadline)?, false)?;
            attempt.progress("Generating deck").await?;
            outcome = match attempt
                .model(
                    NewslyTranscript::default(),
                    build_user_prompt(source, task.interests_prompt.as_deref()),
                    budget.initial_deadline(),
                )
                .await
            {
                Ok(outcome) => outcome,
                Err(error)
                    if matches!(
                        &error,
                        LearningDeckAgentError::Agent(AgentRuntimeError::DeadlineExceeded)
                    ) && attempt.last_candidate.is_some() =>
                {
                    attempt
                        .events
                        .push("initial_phase_deadline", error.log_payload());
                    interrupted = true;
                    empty_outcome(&self.config.model_spec.canonical())
                }
                Err(error) => return Err(error),
            };
            validation = Some(attempt.capture_and_validate(interrupted).await);
        }
        let report = match validation.expect("initial or restored candidate was checked") {
            Ok(report) => report,
            Err(error) if error.repairable() => {
                attempt
                    .events
                    .push("artifact_validation_failed", error.log_payload());
                attempt.progress("Fixing deck layout").await?;
                if interrupted && let Some(candidate) = &attempt.last_candidate {
                    restore_candidate(&attempt.tools, &self.config.limits, candidate).await?;
                }
                let prompt = format!(
                    "Repair the existing output files using input/source.txt and output/source-notes.md; do not repeat completed research. Fix this canonical validation report: {}. Call validate_deck after edits. Keep complete output/index.html and output/source-notes.md on disk throughout.",
                    error.log_payload()
                );
                let mut repair_interrupted = false;
                match attempt
                    .model(outcome.transcript.clone(), prompt, budget.repair_deadline())
                    .await
                {
                    Ok(mut repair) => {
                        repair.usage.add_assign(&outcome.usage);
                        repair.request_count =
                            repair.request_count.saturating_add(outcome.request_count);
                        repair.tool_call_count = repair
                            .tool_call_count
                            .saturating_add(outcome.tool_call_count);
                        outcome = repair;
                    }
                    Err(LearningDeckAgentError::Agent(AgentRuntimeError::DeadlineExceeded))
                        if attempt.last_candidate.is_some() =>
                    {
                        repair_interrupted = true;
                    }
                    Err(error) => return Err(error),
                }
                attempt.capture_and_validate(repair_interrupted).await?
            }
            Err(error) => return Err(error),
        };
        let candidate = attempt.last_candidate.take().ok_or_else(|| {
            LearningDeckAgentError::Bootstrap("validated candidate is absent".to_owned())
        })?;
        attempt
            .events
            .push("browser_validation_passed", report.clone());
        Ok(LearningDeckAgentRunResult {
            deadline: budget.deadline,
            index_html: hosted_viewer_html(&candidate.index_html),
            source_notes_md: candidate.source_notes,
            assets: candidate.assets,
            source_metadata_updates: candidate.source_metadata,
            browser_validation: report.as_object().cloned().unwrap_or_default(),
            model_provider: outcome
                .model_name
                .split_once(':')
                .map_or("unknown", |(provider, _)| provider)
                .to_owned(),
            sandbox_provider: "e2b".to_owned(),
            sandbox_id: sandbox.sandbox_id.as_str().to_owned(),
            outcome,
            events: attempt.events.values(),
        })
    }
}

impl Attempt<'_> {
    async fn progress(&self, note: &str) -> Result<(), LearningDeckAgentError> {
        if !publish_learning_deck_progress(&self.runtime.pool, self.fence, note).await? {
            return Err(LearningDeckAgentError::Cancelled);
        }
        self.events.push("generation_stage", json!({"note":note}));
        Ok(())
    }
    fn validator(&self, deadline: Instant) -> Arc<DeckValidator> {
        Arc::new(DeckValidator::new(
            self.runtime.provider.clone(),
            self.sandbox.clone(),
            self.tools.clone(),
            deadline,
            self.cancellation.child_token(),
            self.runtime.config.limits.clone(),
        ))
    }
    async fn validate(&self, candidate: &DeckCandidate) -> Result<Value, LearningDeckAgentError> {
        self.progress("Checking slides in portrait and landscape")
            .await?;
        self.validator(self.budget.deadline - Duration::from_secs(60))
            .validate_candidate(candidate)
            .await
    }
    async fn checkpoint(&mut self) -> Result<(), LearningDeckAgentError> {
        let candidate = capture_candidate(&self.tools, &self.runtime.config.limits).await?;
        super::super::artifacts::validate_learning_deck_artifact(
            &hosted_viewer_html(&candidate.index_html),
            &candidate.source_notes,
            &self.runtime.config.limits,
        )
        .map_err(LearningDeckAgentError::from_artifact)?;
        let hash = candidate_digest(&candidate)?;
        if self.last_digest.as_deref() != Some(&hash) {
            let key = self
                .artifacts
                .store_draft(
                    self.task.user_id,
                    self.task.deck_id,
                    self.task.id,
                    self.fingerprint,
                    &candidate,
                )
                .await?;
            if !publish_learning_deck_draft(&self.runtime.pool, self.fence, self.fingerprint, &key)
                .await?
            {
                return Err(LearningDeckAgentError::Cancelled);
            }
            self.events
                .push("draft_checkpoint", json!({"assets":candidate.assets.len()}));
            self.last_digest = Some(hash);
        }
        self.last_candidate = Some(candidate);
        Ok(())
    }
    async fn capture_and_validate(
        &mut self,
        interrupted: bool,
    ) -> Result<Value, LearningDeckAgentError> {
        // Interrupted model work already attempted its final checkpoint. Reuse captured bytes
        // so another complete download cannot consume the reserved browser-validation time.
        let result = if interrupted {
            Ok(())
        } else {
            self.checkpoint().await
        };
        let candidate =
            candidate_for_validation(result, self.last_candidate.as_ref(), interrupted)?;
        self.validate(candidate).await
    }

    async fn model(
        &mut self,
        transcript: NewslyTranscript,
        prompt: String,
        deadline: Instant,
    ) -> Result<AgentOutcome, LearningDeckAgentError> {
        if GenerationBudget::remaining(deadline)? < Duration::from_secs(15) {
            return Err(LearningDeckAgentError::Deadline);
        }
        let validator = self.validator(deadline);
        let mut definitions = TaskToolExecutor::definitions();
        definitions.push(DeckValidator::definition());
        let request =
            self.runtime
                .agent_request(self.task, transcript, prompt, definitions, deadline)?;
        let runtime = self.runtime;
        let model = runtime
            .engine
            .run(request, validator.clone(), self.events.clone());
        let result = {
            let model = tokio::time::timeout_at(deadline, model);
            tokio::pin!(model);
            let mut checkpoint = tokio::time::interval(Duration::from_secs(30));
            checkpoint.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            checkpoint.tick().await;
            loop {
                tokio::select! {
                    result=&mut model=>break result.map_err(|_|LearningDeckAgentError::Agent(AgentRuntimeError::DeadlineExceeded)).and_then(|value|value.map_err(LearningDeckAgentError::Agent)),
                    ()=self.cancellation.cancelled()=>break Err(LearningDeckAgentError::Cancelled),
                    _=checkpoint.tick()=>{
                        if let Ok(Err(error))=tokio::time::timeout(Duration::from_secs(10),self.checkpoint()).await {self.events.push("draft_checkpoint_deferred",error.log_payload());}
                    }
                }
            }
        };
        if result.is_err() && !self.cancellation.is_cancelled() {
            match tokio::time::timeout(Duration::from_secs(10), self.checkpoint()).await {
                Ok(Ok(())) => {}
                Ok(Err(error)) => self
                    .events
                    .push("draft_checkpoint_deferred", error.log_payload()),
                Err(_) => self.events.push(
                    "draft_checkpoint_deferred",
                    json!({"reason":"checkpoint time budget exhausted"}),
                ),
            }
        }
        if let Some(error) = validator.take_failure() {
            return Err(error);
        }
        result
    }
}
fn candidate_for_validation(
    capture: Result<(), LearningDeckAgentError>,
    saved: Option<&DeckCandidate>,
    interrupted: bool,
) -> Result<&DeckCandidate, LearningDeckAgentError> {
    if let Err(error) = capture
        && !(interrupted && error.repairable() && saved.is_some())
    {
        return Err(error);
    }
    saved.ok_or_else(|| {
        LearningDeckAgentError::Bootstrap("checkpoint contains no candidate".to_owned())
    })
}

fn require_attempt_budget(
    remaining: Duration,
    has_draft: bool,
) -> Result<(), LearningDeckAgentError> {
    let minimum = super::minimum_generation_budget(has_draft);
    if remaining < minimum {
        Err(LearningDeckAgentError::Deadline)
    } else {
        Ok(())
    }
}

fn input_fingerprint(
    source: &Map<String, Value>,
    interests: Option<&str>,
    tool_policy: &Map<String, Value>,
) -> Result<String, serde_json::Error> {
    Ok(digest(&serde_json::to_vec(
        &json!({"source":source,"interests":interests,"tool_policy":tool_policy,"prompt":include_str!("../../../../../assets/prompts/learning_decks/agent.md"),"house":HOUSE_DESIGN_APPENDIX,"validator":include_str!("../browser.rs"),"draft_version":1}),
    )?))
}
fn candidate_digest(candidate: &DeckCandidate) -> Result<String, serde_json::Error> {
    Ok(digest(&serde_json::to_vec(
        &json!({"html":candidate.index_html,"notes":candidate.source_notes,"metadata":candidate.source_metadata,"assets":candidate.assets.iter().map(|asset|json!({"path":asset.relative_path,"digest":digest(&asset.bytes)})).collect::<Vec<_>>()}),
    )?))
}
fn empty_outcome(model: &str) -> AgentOutcome {
    AgentOutcome {
        output_text: String::new(),
        structured_output: None,
        transcript: NewslyTranscript::default(),
        usage: newsly_agent_runtime::ProviderUsage::default(),
        provider_response_id: None,
        model_name: model.to_owned(),
        request_count: 0,
        tool_call_count: 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checkpoint_identity_binds_source_instructions_and_tool_policy() {
        let source = Map::from_iter([("body_text".to_owned(), json!("original"))]);
        let policy = Map::new();
        let original = input_fingerprint(&source, Some("focus"), &policy).unwrap();
        assert_eq!(original.len(), 64);
        let mut changed = source.clone();
        changed.insert("body_text".to_owned(), json!("updated"));
        assert_ne!(
            original,
            input_fingerprint(&changed, Some("focus"), &policy).unwrap()
        );
        assert_ne!(
            original,
            input_fingerprint(&source, Some("different"), &policy).unwrap()
        );
        assert_ne!(
            original,
            input_fingerprint(
                &source,
                Some("focus"),
                &Map::from_iter([("web_search".to_owned(), json!(false))])
            )
            .unwrap()
        );
    }

    #[test]
    fn candidate_identity_includes_binary_assets_and_source_metadata() {
        let candidate = DeckCandidate {
            index_html: "html".to_owned(),
            source_notes: "notes".to_owned(),
            source_metadata: Map::new(),
            assets: vec![crate::learning_deck::artifacts::LearningDeckAsset {
                relative_path: "assets/diagram.svg".to_owned(),
                content_type: "image/svg+xml".to_owned(),
                bytes: b"first".to_vec(),
            }],
        };
        let original = candidate_digest(&candidate).unwrap();
        let mut changed = candidate.clone();
        changed.assets[0].bytes = b"other".to_vec();
        assert_ne!(original, candidate_digest(&changed).unwrap());
        changed = candidate;
        changed
            .source_metadata
            .insert("commit_sha".to_owned(), json!("different"));
        assert_ne!(original, candidate_digest(&changed).unwrap());
    }
    #[test]
    fn only_interrupted_edits_can_fall_back_to_a_saved_candidate() {
        let saved = DeckCandidate {
            index_html: "older complete deck".to_owned(),
            source_notes: "notes".to_owned(),
            source_metadata: Map::new(),
            assets: vec![],
        };
        let invalid = || LearningDeckAgentError::Artifact {
            message: "new final output has forbidden script".to_owned(),
            report: Map::new(),
            repairable: true,
        };
        // Normal completion must repair the new files instead of publishing or restoring old work.
        assert!(candidate_for_validation(Err(invalid()), Some(&saved), false).is_err());
        assert_eq!(
            candidate_for_validation(Err(invalid()), Some(&saved), true)
                .unwrap()
                .index_html,
            "older complete deck"
        );
        assert!(candidate_for_validation(Err(invalid()), None, true).is_err());
        assert!(
            candidate_for_validation(
                Err(LearningDeckAgentError::E2b(newsly_e2b::E2bError::Deadline)),
                Some(&saved),
                true
            )
            .is_err()
        );
    }

    #[test]
    fn retries_without_a_draft_do_not_create_vms_after_the_generation_cutoff() {
        assert!(require_attempt_budget(Duration::from_secs(170), false).is_err());
        assert!(require_attempt_budget(Duration::from_secs(194), false).is_err());
        assert!(require_attempt_budget(Duration::from_secs(195), false).is_ok());
        assert!(require_attempt_budget(Duration::from_secs(170), true).is_ok());
        assert!(require_attempt_budget(Duration::from_secs(119), true).is_err());
    }
}
