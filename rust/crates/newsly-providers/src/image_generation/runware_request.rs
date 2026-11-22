use serde::Serialize;

use super::{DEFAULT_RUNWARE_MODEL, PODCAST_RUNWARE_MODEL};

const SEEDREAM_INFOGRAPHIC_WIDTH: u32 = 2_848;
const SEEDREAM_INFOGRAPHIC_HEIGHT: u32 = 1_600;
const DEFAULT_INFOGRAPHIC_WIDTH: u32 = 1_024;
const DEFAULT_INFOGRAPHIC_HEIGHT: u32 = 576;
const INFOGRAPHIC_NEGATIVE_PROMPT: &str = "readable text, words, letters, numbers, captions, labels, headlines, logos, watermarks, screenshots, website UI, app interface, chart axes, poster, document page, printed page, magazine spread, dashboard, phone screen, tablet screen, desktop monitor, laptop, computer, office workstation";

#[derive(Debug, Clone, Copy)]
struct RunwareOptions {
    width: u32,
    height: u32,
    negative_prompt: Option<&'static str>,
    settings: Option<RunwareSettings>,
}

fn runware_request_options(model: &str) -> RunwareOptions {
    if model == PODCAST_RUNWARE_MODEL {
        return RunwareOptions {
            width: 2_048,
            height: 1_152,
            negative_prompt: None,
            settings: Some(RunwareSettings {
                thinking_level: "high",
                web_search: false,
                image_search: false,
                shell: false,
            }),
        };
    }
    if model == DEFAULT_RUNWARE_MODEL {
        return RunwareOptions {
            width: SEEDREAM_INFOGRAPHIC_WIDTH,
            height: SEEDREAM_INFOGRAPHIC_HEIGHT,
            negative_prompt: None,
            settings: None,
        };
    }
    RunwareOptions {
        width: DEFAULT_INFOGRAPHIC_WIDTH,
        height: DEFAULT_INFOGRAPHIC_HEIGHT,
        negative_prompt: Some(INFOGRAPHIC_NEGATIVE_PROMPT),
        settings: None,
    }
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
struct RunwareSettings {
    thinking_level: &'static str,
    web_search: bool,
    image_search: bool,
    shell: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct RunwareRequest<'a> {
    task_type: &'static str,
    #[serde(rename = "taskUUID")]
    pub(super) task_uuid: &'a str,
    include_cost: bool,
    output_type: &'static str,
    output_format: &'static str,
    positive_prompt: &'a str,
    pub(super) model: &'a str,
    number_results: u8,
    pub(super) width: u32,
    pub(super) height: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    negative_prompt: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    settings: Option<RunwareSettings>,
}

impl<'a> RunwareRequest<'a> {
    pub(super) fn new(prompt: &'a str, model: &'a str, task_uuid: &'a str) -> Self {
        let options = runware_request_options(model);
        Self {
            task_type: "imageInference",
            task_uuid,
            include_cost: true,
            output_type: "URL",
            output_format: "PNG",
            positive_prompt: prompt,
            model,
            number_results: 1,
            width: options.width,
            height: options.height,
            negative_prompt: options.negative_prompt,
            settings: options.settings,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    use uuid::Uuid;

    #[test]
    fn runware_request_uses_provider_exact_task_uuid_field() {
        let task_uuid = "a770f077-f413-47de-9dac-be0b26a35da6";
        let request =
            RunwareRequest::new("A bounded fixture prompt", DEFAULT_RUNWARE_MODEL, task_uuid);

        let payload = serde_json::to_value([request]).expect("Runware request should serialize");
        let task = payload
            .as_array()
            .and_then(|tasks| tasks.first())
            .and_then(Value::as_object)
            .expect("Runware request should be a one-task array");

        assert_eq!(task.get("taskUUID"), Some(&Value::from(task_uuid)));
        assert!(!task.contains_key("taskUuid"));
        let parsed = Uuid::parse_str(task_uuid).expect("fixture task UUID should parse");
        assert_eq!(parsed.get_version_num(), 4);
    }

    #[test]
    fn muse_request_matches_evaluated_wire_payload() {
        let task_uuid = "a770f077-f413-47de-9dac-be0b26a35da6";
        let request = RunwareRequest::new("A bounded fixture prompt", "meta:muse@image", task_uuid);
        assert_eq!(
            serde_json::to_value([request]).unwrap(),
            json!([{
                "taskType": "imageInference",
                "taskUUID": task_uuid,
                "includeCost": true,
                "outputType": "URL",
                "outputFormat": "PNG",
                "positivePrompt": "A bounded fixture prompt",
                "model": "meta:muse@image",
                "numberResults": 1,
                "width": 2048,
                "height": 1152,
                "settings": {
                    "thinkingLevel": "high",
                    "webSearch": false,
                    "imageSearch": false,
                    "shell": false
                }
            }])
        );
    }

    #[test]
    fn existing_runware_models_preserve_wire_options() {
        for (model, width, height, negative_prompt) in [
            (DEFAULT_RUNWARE_MODEL, 2848, 1600, None),
            (
                "runware:101@1",
                1024,
                576,
                Some(INFOGRAPHIC_NEGATIVE_PROMPT),
            ),
            (
                "meta:muse@other",
                1024,
                576,
                Some(INFOGRAPHIC_NEGATIVE_PROMPT),
            ),
        ] {
            let payload =
                serde_json::to_value(RunwareRequest::new("prompt", model, "task")).unwrap();
            assert_eq!(payload["width"], width);
            assert_eq!(payload["height"], height);
            assert_eq!(payload["outputFormat"], "PNG");
            assert!(payload.get("settings").is_none());
            assert_eq!(
                payload.get("negativePrompt").and_then(Value::as_str),
                negative_prompt
            );
            if negative_prompt.is_none() {
                assert!(payload.get("negativePrompt").is_none());
            }
        }
    }

    #[test]
    fn seedream_uses_provider_native_dimensions_without_negative_prompt() {
        let options = runware_request_options(DEFAULT_RUNWARE_MODEL);
        assert_eq!(options.width, 2_848);
        assert_eq!(options.height, 1_600);
        assert_eq!(options.negative_prompt, None);
    }

    #[test]
    fn other_runware_models_use_standard_infographic_request() {
        let options = runware_request_options("runware:101@1");
        assert_eq!(options.width, 1_024);
        assert_eq!(options.height, 576);
        assert!(options.negative_prompt.is_some());
    }
}
