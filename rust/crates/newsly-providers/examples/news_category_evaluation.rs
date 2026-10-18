//! Opt-in, resumable 120-call Rust gateway evaluation over frozen public heldout evidence.
use std::{collections::BTreeSet, io::Write, path::Path};

use newsly_providers::{NewsCategory, NewsCategoryGateway, NewsCategoryInput};
use secrecy::SecretString;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

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
    }
    if inputs.len() != 120 {
        return Err("manifest must contain 120 distinct public definition jobs".into());
    }
    Ok(inputs)
}

fn digest(bytes: &[u8]) -> String {
    const HEX: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(64);
    for byte in Sha256::digest(bytes) {
        result.push(char::from(HEX[usize::from(byte >> 4)]));
        result.push(char::from(HEX[usize::from(byte & 15)]));
    }
    result
}

fn durable_json(path: &Path, value: &Value, create: bool) -> Result<(), Error> {
    let target = if create {
        path.to_path_buf()
    } else {
        path.with_extension("tmp")
    };
    let mut options = std::fs::OpenOptions::new();
    options.write(true);
    if create {
        options.create_new(true);
    } else {
        options.create(true).truncate(true);
    }
    let mut file = options.open(&target)?;
    file.write_all(&serde_json::to_vec_pretty(value)?)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    if !create {
        std::fs::rename(&target, path)?;
    }
    std::fs::File::open(path.parent().ok_or("missing parent")?)?.sync_all()?;
    Ok(())
}

struct RunLock(std::path::PathBuf);
impl Drop for RunLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

async fn observe(gateway: &NewsCategoryGateway, job: &str, input: &NewsCategoryInput) -> Value {
    let start = std::time::Instant::now();
    let mut observation = match gateway.decide(input).await {
        Ok(decision) => json!({"job_id":job,"status":"completed","ok":true,
                "selected_key":decision.selected_key,"probability":decision.selected_probability,
                "usage":decision.usage,"cost_usd":decision.cost_usd,
                "generation_id":decision.generation_id}),
        Err(error) => json!({"job_id":job,"status":"failed","ok":false,
                "error_code":error.code(),"usage":error.observed_usage(),
                "cost_usd":error.observed_cost_usd(),"generation_id":error.generation_id()}),
    };
    observation["elapsed_ms"] = json!(start.elapsed().as_millis());
    observation
}

fn validate_ids(heldout: &Value, inputs: &[(String, NewsCategoryInput)]) -> Result<(), Error> {
    let expected = heldout
        .as_array()
        .ok_or("missing heldout stories")?
        .iter()
        .map(|story| story["news_id"].to_string())
        .collect::<BTreeSet<_>>();
    let actual = inputs
        .iter()
        .map(|(job, _)| {
            job.strip_suffix("-definitions")
                .ok_or("invalid definition job ID")
                .map(str::to_owned)
        })
        .collect::<Result<BTreeSet<_>, _>>()?;
    if expected.len() != 120 || expected != actual {
        return Err("heldout ID mismatch".into());
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 || args[0] != "--allow-live-provider-costs" {
        return Err(
            "usage: news_category_evaluation --allow-live-provider-costs MANIFEST HELDOUT OUTPUT"
                .into(),
        );
    }
    let manifest_bytes = std::fs::read(&args[1])?;
    let heldout_bytes = std::fs::read(&args[2])?;
    let manifest: Value = serde_json::from_slice(&manifest_bytes)?;
    let heldout: Value = serde_json::from_slice(&heldout_bytes)?;
    if digest(&heldout_bytes) != text(&manifest, "heldout_sha256")? {
        return Err("heldout hash differs from frozen manifest".into());
    }
    let inputs = public_inputs(&manifest)?;
    validate_ids(&heldout, &inputs)?;
    let worst_case_usd = 120.0 * (65_536.0 + 4_096.0) * 0.042 / 1_000_000.0;
    if worst_case_usd > 0.36 {
        return Err("evaluation exceeds authorized spend".into());
    }
    let output = Path::new(&args[3]);
    std::fs::create_dir_all(output)?;
    let lock_path = output.join("run.lock");
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&lock_path)?;
    let _lock = RunLock(lock_path);
    let binding = json!({
        "version":1,"max_calls":120,"max_cost_usd":worst_case_usd,
        "manifest_sha256":digest(&manifest_bytes),
        "heldout_sha256":digest(&heldout_bytes),
        "adapter_sha256":digest(include_bytes!("../src/news_categories.rs")),
        "runner_sha256":digest(include_bytes!("news_category_evaluation.rs")),
    });
    let binding_path = output.join("binding.json");
    if binding_path.exists() {
        let saved: Value = serde_json::from_slice(&std::fs::read(&binding_path)?)?;
        if saved != binding {
            return Err("frozen evaluation binding changed".into());
        }
    } else {
        durable_json(&binding_path, &binding, true)?;
    }
    let key = SecretString::from(
        std::env::var("OPENROUTER_API_KEY").map_err(|_| "OPENROUTER_API_KEY is required")?,
    );
    let gateway = NewsCategoryGateway::new(Some(key))?;
    let mut generations = BTreeSet::new();
    let mut observations = Vec::new();
    for (index, (job, input)) in inputs.into_iter().enumerate() {
        let path = output.join(format!("{:03}.json", index + 1));
        if path.exists() {
            let saved: Value = serde_json::from_slice(&std::fs::read(&path)?)?;
            if saved["job_id"] != job {
                return Err("artifact job mismatch".into());
            }
            if let Some(id) = saved["generation_id"].as_str()
                && !generations.insert(id.to_owned())
            {
                return Err("duplicate generation ID".into());
            }
            observations.push(saved);
            continue;
        }
        durable_json(
            &path,
            &json!({"job_id":job,"status":"reserved",
            "reserved_usd":worst_case_usd/120.0}),
            true,
        )?;
        let mut observation = observe(&gateway, &job, &input).await;
        observation["reserved_usd"] = json!(worst_case_usd / 120.0);
        durable_json(&path, &observation, false)?;
        if let Some(id) = observation["generation_id"].as_str()
            && !generations.insert(id.to_owned())
        {
            return Err("duplicate generation ID".into());
        }
        println!(
            "{}",
            json!({"attempt":index+1,"job_id":job,"ok":observation["ok"],
            "cost_usd":observation["cost_usd"],"elapsed_ms":observation["elapsed_ms"]})
        );
        observations.push(observation);
    }
    let summary = json!({"attempts":observations.len(),"completed":observations.iter()
        .filter(|o|o["ok"]==true).count(),"cost_or_reserved_usd":observations.iter()
        .map(|o|o["cost_usd"].as_f64().unwrap_or(worst_case_usd/120.0)).sum::<f64>()});
    durable_json(&output.join("summary.json"), &summary, false)?;
    println!("{summary}");
    Ok(())
}
