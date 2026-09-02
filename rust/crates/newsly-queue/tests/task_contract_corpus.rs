use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};

use newsly_queue::{TaskQueue, TaskType};
use serde::Deserialize;
use sha2::{Digest, Sha256};

const TASK_CATALOG_VERSION: u32 = 1;
const OWNERSHIP_MANIFEST_VERSION: u32 = 2;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskCatalog {
    version: u32,
    tasks: Vec<CatalogTask>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CatalogTask {
    task_type: TaskType,
    queue: TaskQueue,
    handler: String,
    payload_schema: String,
    payload_schema_sha256: String,
    dedupe_by_content: bool,
    requires_owner: bool,
}

#[derive(Debug, Deserialize)]
struct OwnershipManifest {
    version: u32,
    runtime_registry: String,
    tasks: Vec<PolicyTask>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyTask {
    task_type: TaskType,
    queue: TaskQueue,
    payload_schema: String,
    handler: String,
    current_owner: String,
    database_writer: String,
    e2b_namespace: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FixtureIndex {
    version: u32,
    artifacts: Vec<FixtureArtifact>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FixtureArtifact {
    category: String,
    path: String,
    sha256: String,
}

#[test]
fn task_contract_corpus_is_complete_and_rust_owned() {
    let contracts_root = contracts_root();
    let catalog_path = contracts_root.join("tasks/catalog.json");
    let catalog: TaskCatalog = parse_json(&catalog_path);
    let manifest: OwnershipManifest = parse_toml(&contracts_root.join("policy-manifest.toml"));
    let fixture_index: FixtureIndex = parse_json(&contracts_root.join("fixtures/index.json"));

    assert_eq!(catalog.version, TASK_CATALOG_VERSION);
    assert_eq!(fixture_index.version, TASK_CATALOG_VERSION);
    assert_eq!(manifest.version, OWNERSHIP_MANIFEST_VERSION);
    assert_eq!(manifest.runtime_registry, "runtime_ownership");
    assert_eq!(
        TaskType::ALL.len(),
        21,
        "update the frozen task-count assertion"
    );

    let declared_task_types: BTreeSet<_> = TaskType::ALL.iter().copied().collect();
    assert_eq!(declared_task_types.len(), TaskType::ALL.len());

    let catalog_by_type = index_catalog(catalog.tasks);
    let policy_by_type = index_policy(manifest.tasks);
    assert_eq!(catalog_by_type.len(), TaskType::ALL.len());
    assert_eq!(policy_by_type.len(), TaskType::ALL.len());
    let catalog_task_types: BTreeSet<_> = catalog_by_type.keys().copied().collect();
    let policy_task_types: BTreeSet<_> = policy_by_type.keys().copied().collect();
    assert_eq!(catalog_task_types, declared_task_types);
    assert_eq!(policy_task_types, declared_task_types);

    let fixture_by_path = index_fixtures(fixture_index.artifacts);
    let mut registered_schema_paths = BTreeSet::new();

    for &task_type in TaskType::ALL {
        let catalog_task = catalog_by_type
            .get(&task_type)
            .expect("the set comparison proves this entry exists");
        let policy_task = policy_by_type
            .get(&task_type)
            .expect("the set comparison proves this entry exists");
        let spec = task_type.spec();
        let expected_schema_path = format!("tasks/{task_type}.schema.json");

        assert_eq!(
            catalog_task.queue, spec.queue,
            "queue mismatch for {task_type}"
        );
        assert_eq!(
            catalog_task.handler, spec.handler,
            "handler mismatch for {task_type}"
        );
        assert_eq!(
            catalog_task.dedupe_by_content, spec.dedupe_by_content,
            "dedupe mismatch for {task_type}"
        );
        assert_eq!(
            catalog_task.requires_owner, spec.requires_owner,
            "user-ownership mismatch for {task_type}"
        );
        assert_eq!(
            catalog_task.payload_schema, expected_schema_path,
            "noncanonical payload schema path for {task_type}"
        );

        let schema_path = contracts_root.join(&catalog_task.payload_schema);
        assert!(
            schema_path.is_file(),
            "missing schema for {task_type}: {schema_path:?}"
        );
        let schema_sha256 = sha256_file(&schema_path);
        assert_eq!(
            catalog_task.payload_schema_sha256, schema_sha256,
            "payload schema hash mismatch for {task_type}"
        );
        assert!(registered_schema_paths.insert(catalog_task.payload_schema.clone()));

        assert_eq!(
            policy_task.queue, spec.queue,
            "policy queue mismatch for {task_type}"
        );
        assert_eq!(
            policy_task.handler, spec.handler,
            "policy handler mismatch for {task_type}"
        );
        assert_eq!(policy_task.payload_schema, catalog_task.payload_schema);
        assert_eq!(policy_task.current_owner, "rust");
        assert_eq!(policy_task.database_writer, "rust");
        assert!(!policy_task.e2b_namespace.trim().is_empty());

        let fixture = fixture_by_path
            .get(&catalog_task.payload_schema)
            .unwrap_or_else(|| panic!("fixture index omits {}", catalog_task.payload_schema));
        assert_eq!(fixture.category, "tasks");
        assert_eq!(fixture.sha256, schema_sha256);
    }

    assert_eq!(
        registered_schema_paths,
        task_payload_schema_paths(&contracts_root)
    );

    let catalog_fixture = fixture_by_path
        .get("tasks/catalog.json")
        .expect("fixture index must contain the task catalog");
    assert_eq!(catalog_fixture.category, "tasks");
    assert_eq!(catalog_fixture.sha256, sha256_file(&catalog_path));
}

fn index_catalog(tasks: Vec<CatalogTask>) -> BTreeMap<TaskType, CatalogTask> {
    let mut indexed = BTreeMap::new();
    for task in tasks {
        let task_type = task.task_type;
        assert!(
            indexed.insert(task_type, task).is_none(),
            "duplicate catalog task {task_type}"
        );
    }
    indexed
}

fn index_policy(tasks: Vec<PolicyTask>) -> BTreeMap<TaskType, PolicyTask> {
    let mut indexed = BTreeMap::new();
    for task in tasks {
        let task_type = task.task_type;
        assert!(
            indexed.insert(task_type, task).is_none(),
            "duplicate policy task {task_type}"
        );
    }
    indexed
}

fn index_fixtures(artifacts: Vec<FixtureArtifact>) -> BTreeMap<String, FixtureArtifact> {
    let mut indexed = BTreeMap::new();
    for artifact in artifacts {
        let path = artifact.path.clone();
        assert!(
            indexed.insert(path.clone(), artifact).is_none(),
            "duplicate fixture path {path}"
        );
    }
    indexed
}

fn task_payload_schema_paths(contracts_root: &Path) -> BTreeSet<String> {
    fs::read_dir(contracts_root.join("tasks"))
        .expect("task contract directory should be readable")
        .map(|entry| {
            entry
                .expect("task contract entry should be readable")
                .path()
        })
        .filter(|path| {
            path.extension().and_then(|extension| extension.to_str()) == Some("json")
                && path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| {
                        name.ends_with(".schema.json") && name != "queue-kernel.schema.json"
                    })
        })
        .map(|path| {
            format!(
                "tasks/{}",
                path.file_name()
                    .and_then(|name| name.to_str())
                    .expect("schema path should be UTF-8")
            )
        })
        .collect()
}

fn parse_json<T: for<'de> Deserialize<'de>>(path: &Path) -> T {
    serde_json::from_slice(
        &fs::read(path).unwrap_or_else(|error| panic!("read {}: {error}", path.display())),
    )
    .unwrap_or_else(|error| panic!("parse {}: {error}", path.display()))
}

fn parse_toml<T: for<'de> Deserialize<'de>>(path: &Path) -> T {
    toml::from_slice(
        &fs::read(path).unwrap_or_else(|error| panic!("read {}: {error}", path.display())),
    )
    .unwrap_or_else(|error| panic!("parse {}: {error}", path.display()))
}

fn sha256_file(path: &Path) -> String {
    let digest = Sha256::digest(
        fs::read(path).unwrap_or_else(|error| panic!("read {}: {error}", path.display())),
    );
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    encoded
}

fn contracts_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../contracts")
}
