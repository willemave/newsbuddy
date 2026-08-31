use std::collections::{BTreeMap, BTreeSet};

use anyhow::{Context, bail, ensure};
use serde::Deserialize;
use serde_json::Value;

use crate::POLICY_VERSION;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClientTarget {
    AppSwift,
    ShareSwift,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EnumPolicy {
    pub schema: String,
    pub targets: BTreeSet<ClientTarget>,
    pub open: bool,
    pub swift_name: Option<String>,
}

impl EnumPolicy {
    pub fn includes(&self, target: ClientTarget) -> bool {
        self.targets.contains(&target)
    }

    pub fn swift_name(&self) -> String {
        self.swift_name
            .clone()
            .unwrap_or_else(|| format!("API{}", self.schema))
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ModelPolicy {
    pub schema: String,
    pub targets: BTreeSet<ClientTarget>,
    pub swift_name: Option<String>,
}

impl ModelPolicy {
    pub fn includes(&self, target: ClientTarget) -> bool {
        self.targets.contains(&target)
    }

    pub fn swift_name(&self) -> String {
        self.swift_name
            .clone()
            .unwrap_or_else(|| format!("API{}", strip_dto_suffix(&self.schema)))
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct UnionPolicy {
    pub schema: String,
    pub targets: BTreeSet<ClientTarget>,
    pub discriminator: String,
    pub open: bool,
    pub swift_name: Option<String>,
}

impl UnionPolicy {
    pub fn includes(&self, target: ClientTarget) -> bool {
        self.targets.contains(&target)
    }

    pub fn swift_name(&self) -> String {
        self.swift_name
            .clone()
            .unwrap_or_else(|| format!("API{}", strip_dto_suffix(&self.schema)))
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Settings {
    #[serde(default)]
    pub untyped_fields: BTreeSet<String>,
    #[serde(default)]
    pub lenient_fields: BTreeSet<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Policy {
    pub version: u32,
    #[serde(default)]
    pub enums: Vec<EnumPolicy>,
    #[serde(default)]
    pub models: Vec<ModelPolicy>,
    #[serde(default)]
    pub unions: Vec<UnionPolicy>,
    #[serde(default)]
    pub settings: Settings,
    #[serde(default, rename = "field_defaults")]
    raw_field_defaults: BTreeMap<String, String>,
}

impl Policy {
    /// Validate uniqueness, field paths, and encoded defaults.
    ///
    /// # Errors
    ///
    /// Returns an error when the policy uses an unsupported version or contains an invalid entry.
    pub fn validate(&self) -> anyhow::Result<()> {
        ensure!(
            self.version == POLICY_VERSION,
            "unsupported client codegen policy version {}; expected {}",
            self.version,
            POLICY_VERSION
        );
        ensure_unique(self.enums.iter().map(|item| item.schema.as_str()), "enum")?;
        ensure_unique(self.models.iter().map(|item| item.schema.as_str()), "model")?;
        ensure_unique(self.unions.iter().map(|item| item.schema.as_str()), "union")?;
        let mut schema_kinds = BTreeMap::new();
        for (schema, kind) in self
            .enums
            .iter()
            .map(|item| (item.schema.as_str(), "enum"))
            .chain(
                self.models
                    .iter()
                    .map(|item| (item.schema.as_str(), "model")),
            )
            .chain(
                self.unions
                    .iter()
                    .map(|item| (item.schema.as_str(), "union")),
            )
        {
            ensure!(
                schema_kinds.insert(schema, kind).is_none(),
                "schema {schema} is registered as more than one client kind"
            );
        }
        for union in &self.unions {
            ensure!(
                !union.discriminator.is_empty(),
                "union {} has an empty discriminator",
                union.schema
            );
            ensure!(
                union.open,
                "union {} must be open for forward-compatible clients",
                union.schema
            );
        }
        for (path, encoded) in &self.raw_field_defaults {
            ensure_field_path(path)?;
            serde_json::from_str::<Value>(encoded)
                .with_context(|| format!("field default {path} is not a JSON literal"))?;
        }
        for path in self
            .settings
            .untyped_fields
            .iter()
            .chain(&self.settings.lenient_fields)
        {
            ensure_field_path(path)?;
        }
        Ok(())
    }

    pub fn enum_policy(&self, name: &str) -> Option<&EnumPolicy> {
        self.enums.iter().find(|item| item.schema == name)
    }

    pub fn model_policy(&self, name: &str) -> Option<&ModelPolicy> {
        self.models.iter().find(|item| item.schema == name)
    }

    pub fn union_policy(&self, name: &str) -> Option<&UnionPolicy> {
        self.unions.iter().find(|item| item.schema == name)
    }

    pub(crate) fn swift_type_name(&self, schema: &str) -> String {
        if let Some(item) = self.enum_policy(schema) {
            return item.swift_name();
        }
        if let Some(item) = self.model_policy(schema) {
            return item.swift_name();
        }
        if let Some(item) = self.union_policy(schema) {
            return item.swift_name();
        }
        format!("API{}", strip_dto_suffix(schema))
    }

    pub(crate) fn field_default(&self, path: &str) -> anyhow::Result<Option<Value>> {
        self.raw_field_defaults
            .get(path)
            .map(|encoded| {
                serde_json::from_str(encoded)
                    .with_context(|| format!("field default {path} is not valid JSON"))
            })
            .transpose()
    }

    pub(crate) fn field_default_paths(&self) -> impl Iterator<Item = &str> {
        self.raw_field_defaults.keys().map(String::as_str)
    }
}

fn ensure_unique<'a>(values: impl Iterator<Item = &'a str>, label: &str) -> anyhow::Result<()> {
    let mut seen = BTreeSet::new();
    for value in values {
        if !seen.insert(value) {
            bail!("duplicate {label} policy for {value}");
        }
    }
    Ok(())
}

fn ensure_field_path(value: &str) -> anyhow::Result<()> {
    let Some((model, field)) = value.split_once('.') else {
        bail!("client field policy must use Model.field: {value}");
    };
    ensure!(
        !model.is_empty() && !field.is_empty() && !field.contains('.'),
        "invalid client field policy path {value}"
    );
    Ok(())
}

fn strip_dto_suffix(value: &str) -> &str {
    value.strip_suffix("Dto").unwrap_or(value)
}
