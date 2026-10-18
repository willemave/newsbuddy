//! Opt-in three-call Jev canary over public evidence from the frozen category manifest.
use std::collections::BTreeSet;

use newsly_providers::{NewsCategory, NewsCategoryGateway, NewsCategoryInput};
use secrecy::SecretString;
use serde_json::{Value, json};

type Error = Box<dyn std::error::Error>;

fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str, Error> {
    value[field]
        .as_str()
        .ok_or_else(|| format!("missing {field}").into())
}

fn public_inputs(manifest: &Value) -> Result<Vec<(String, NewsCategoryInput)>, Error> {
    let mut seen = BTreeSet::new();
    let mut inputs = Vec::new();
    for job in manifest["jobs"].as_array().ok_or("missing jobs")? {
        if text(job, "variant")? != "definitions" || !seen.insert(text(job, "item_id")?.to_owned())
        {
            continue;
        }
        let request = &job["request"];
        let news = &request["state"]["news"];
        let criteria = request["questions"]["category"]["criteria"]
            .as_object()
            .ok_or("missing criteria")?;
        let mut categories = Vec::new();
        for (key, value) in criteria {
            if key == "other" {
                continue;
            }
            let criterion = value.as_str().ok_or("invalid criterion")?;
            let (title, rest) = criterion.split_once(". ").ok_or("missing category title")?;
            let (deck, rule) = rest
                .split_once(" Routing rule: ")
                .map_or((rest, None), |(deck, rule)| (deck, Some(rule.to_owned())));
            categories.push(NewsCategory {
                key: key.clone(),
                title: title.into(),
                deck: deck.into(),
                routing_rule: rule,
            });
        }
        let key_points = news["key_points"]
            .as_array()
            .ok_or("missing key points")?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| "invalid key point".into())
            })
            .collect::<Result<_, Error>>()?;
        let input = NewsCategoryInput {
            title: text(news, "title")?.into(),
            summary: text(news, "summary")?.into(),
            key_points,
            categories,
        };
        input.validate()?;
        inputs.push((text(job, "job_id")?.to_owned(), input));
        if inputs.len() == 3 {
            break;
        }
    }
    if inputs.len() != 3 {
        return Err("manifest must contain three distinct public definition jobs".into());
    }
    Ok(inputs)
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 || args[0] != "--allow-live-provider-costs" {
        return Err("usage: news_category_canary --allow-live-provider-costs MANIFEST.json".into());
    }
    let manifest: Value = serde_json::from_slice(&std::fs::read(&args[1])?)?;
    let inputs = public_inputs(&manifest)?;
    // The gateway enforces a 64KiB serialized request ceiling and byte-count +4096
    // input-token ceiling. Three maximum-sized requests remain below $0.01.
    let worst_case_usd = 3.0 * (65_536.0 + 4_096.0) * 0.042 / 1_000_000.0;
    if worst_case_usd > 0.01 {
        return Err("canary exceeds authorized maximum spend".into());
    }
    let key = SecretString::from(
        std::env::var("OPENROUTER_API_KEY").map_err(|_| "OPENROUTER_API_KEY is required")?,
    );
    let gateway = NewsCategoryGateway::new(Some(key))?;
    println!("{}", json!({"max_calls":3,"max_cost_usd":worst_case_usd}));
    let mut observations = Vec::new();
    for (job, input) in inputs {
        let start = std::time::Instant::now();
        let observation = match gateway.decide(&input).await {
            Ok(decision) => {
                json!({"job_id":job,"ok":true,"selected_key":decision.selected_key,"probability":decision.selected_probability,"usage":decision.usage,"cost_usd":decision.cost_usd,"generation_id":decision.generation_id})
            }
            Err(error) => {
                json!({"job_id":job,"ok":false,"error_code":error.code(),"usage":error.observed_usage(),"cost_usd":error.observed_cost_usd(),"generation_id":error.generation_id()})
            }
        };
        let mut observation = observation;
        observation["elapsed_ms"] = json!(start.elapsed().as_millis());
        println!("{observation}");
        observations.push(observation);
    }
    if observations.iter().any(|o| o["ok"] != true) {
        return Err("one or more canary calls failed; see redacted observations".into());
    }
    Ok(())
}
