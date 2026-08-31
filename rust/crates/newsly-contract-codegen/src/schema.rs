use std::collections::{BTreeMap, BTreeSet, VecDeque};

use anyhow::{Context, bail, ensure};
use serde_json::{Map, Value};

use crate::policy::{ClientTarget, Policy};

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum TypeKind {
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Enum(String),
    Model(String),
    Union(String),
    List(Box<TypeRef>),
    Map(Box<TypeRef>),
    Untyped,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct TypeRef {
    pub kind: TypeKind,
    pub nullable: bool,
}

impl TypeRef {
    fn with_nullable(mut self, nullable: bool) -> Self {
        self.nullable |= nullable;
        self
    }
}

#[derive(Debug, Clone)]
pub(crate) struct Field {
    pub wire_name: String,
    pub type_ref: TypeRef,
    pub required: bool,
    pub schema_default: Option<Value>,
}

#[derive(Debug, Clone)]
pub(crate) struct Model {
    pub name: String,
    pub fields: Vec<Field>,
}

#[derive(Debug, Clone)]
pub(crate) struct UnionVariant {
    pub tag: String,
    pub model: String,
}

#[derive(Debug, Clone)]
pub(crate) struct Union {
    pub name: String,
    pub discriminator: String,
    pub variants: Vec<UnionVariant>,
}

#[derive(Debug, Clone)]
pub(crate) enum EnumValues {
    Strings(Vec<String>),
    Integers(Vec<i64>),
}

#[derive(Debug, Clone)]
pub(crate) struct Document {
    schemas: BTreeMap<String, Value>,
}

impl Document {
    pub(crate) fn parse(source: &str, policy: &Policy) -> anyhow::Result<Self> {
        let root: Value = serde_json::from_str(source).context("invalid OpenAPI JSON")?;
        let schemas = root
            .pointer("/components/schemas")
            .and_then(Value::as_object)
            .context("OpenAPI document has no components.schemas object")?
            .iter()
            .map(|(name, schema)| (name.clone(), schema.clone()))
            .collect();
        let document = Self { schemas };
        document.validate_policy(policy)?;
        Ok(document)
    }

    pub(crate) fn enum_values(&self, name: &str) -> anyhow::Result<EnumValues> {
        let schema = self.schema(name)?;
        let values = schema
            .get("enum")
            .and_then(Value::as_array)
            .with_context(|| {
                format!("enum policy {name} points to a schema without enum values")
            })?;
        ensure!(!values.is_empty(), "enum schema {name} has no values");
        if values.iter().all(Value::is_string) {
            return Ok(EnumValues::Strings(
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .map(ToOwned::to_owned)
                    .collect(),
            ));
        }
        if values.iter().all(Value::is_i64) {
            return Ok(EnumValues::Integers(
                values.iter().filter_map(Value::as_i64).collect(),
            ));
        }
        bail!("enum schema {name} mixes unsupported value types")
    }

    pub(crate) fn model(&self, name: &str, policy: &Policy) -> anyhow::Result<Model> {
        self.model_omitting(name, policy, None)
    }

    fn model_omitting(
        &self,
        name: &str,
        policy: &Policy,
        omitted_field: Option<&str>,
    ) -> anyhow::Result<Model> {
        let schema = self.schema(name)?;
        let properties = schema
            .get("properties")
            .and_then(Value::as_object)
            .with_context(|| format!("model policy {name} points to a non-object schema"))?;
        let required = schema
            .get("required")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<BTreeSet<_>>()
            })
            .unwrap_or_default();
        let fields = properties
            .iter()
            .filter(|(wire_name, _)| Some(wire_name.as_str()) != omitted_field)
            .map(|(wire_name, field_schema)| {
                let path = format!("{name}.{wire_name}");
                let type_ref = self.parse_type(field_schema, &path, policy)?;
                Ok(Field {
                    wire_name: wire_name.clone(),
                    type_ref,
                    required: required.contains(wire_name.as_str()),
                    schema_default: field_schema.get("default").cloned(),
                })
            })
            .collect::<anyhow::Result<Vec<_>>>()?;
        Ok(Model {
            name: name.to_owned(),
            fields,
        })
    }

    pub(crate) fn union(&self, name: &str, policy: &Policy) -> anyhow::Result<Union> {
        let union_policy = policy
            .union_policy(name)
            .with_context(|| format!("union schema {name} is not in the reviewed registry"))?;
        let schema = self.schema(name)?;
        let members = schema
            .get("oneOf")
            .and_then(Value::as_array)
            .with_context(|| format!("union policy {name} points to a schema without oneOf"))?;
        ensure!(!members.is_empty(), "union schema {name} has no variants");
        let discriminator = schema
            .pointer("/discriminator/propertyName")
            .and_then(Value::as_str)
            .with_context(|| format!("union schema {name} has no discriminator.propertyName"))?;
        ensure!(
            discriminator == union_policy.discriminator,
            "union {name} discriminator is {discriminator}, policy expects {}",
            union_policy.discriminator
        );
        let mapping = schema
            .pointer("/discriminator/mapping")
            .and_then(Value::as_object)
            .map(|items| {
                items
                    .iter()
                    .map(|(tag, reference)| {
                        let reference = reference.as_str().with_context(|| {
                            format!("union {name} discriminator mapping {tag} is not a string")
                        })?;
                        let model = local_reference_name(reference, name)?;
                        Ok((model.to_owned(), tag.clone()))
                    })
                    .collect::<anyhow::Result<BTreeMap<_, _>>>()
            })
            .transpose()?
            .unwrap_or_default();
        ensure!(
            mapping.len()
                == schema
                    .pointer("/discriminator/mapping")
                    .and_then(Value::as_object)
                    .map_or(0, Map::len),
            "union {name} discriminator maps multiple tags to one schema"
        );

        let mut variants = Vec::with_capacity(members.len());
        let mut seen_models = BTreeSet::new();
        let mut seen_tags = BTreeSet::new();
        for (index, member) in members.iter().enumerate() {
            let reference = union_member_reference(member).with_context(|| {
                format!("union {name} variant {index} must be a ref or single-entry allOf ref")
            })?;
            let model = local_reference_name(reference, name)?;
            ensure!(
                seen_models.insert(model.to_owned()),
                "union {name} references variant model {model} more than once"
            );
            ensure!(
                policy.enum_policy(model).is_none() && policy.union_policy(model).is_none(),
                "union {name} variant {model} must be an object model"
            );
            let tag = mapping
                .get(model)
                .cloned()
                .map_or_else(|| self.variant_tag(model, discriminator), Ok)?;
            ensure!(!tag.is_empty(), "union {name} has an empty variant tag");
            ensure!(
                seen_tags.insert(tag.clone()),
                "union {name} repeats discriminator tag {tag}"
            );
            self.validate_discriminator_collision(model, discriminator, &tag)?;
            self.model_omitting(model, policy, Some(discriminator))?;
            variants.push(UnionVariant {
                tag,
                model: model.to_owned(),
            });
        }
        ensure!(
            mapping.keys().all(|model| seen_models.contains(model)),
            "union {name} discriminator mapping names a schema outside oneOf"
        );
        ensure_language_variant_names(name, &variants)?;
        Ok(Union {
            name: name.to_owned(),
            discriminator: discriminator.to_owned(),
            variants,
        })
    }

    pub(crate) fn unions_for_target(
        &self,
        policy: &Policy,
        target: ClientTarget,
    ) -> anyhow::Result<Vec<Union>> {
        policy
            .unions
            .iter()
            .filter(|item| item.includes(target))
            .map(|item| self.union(&item.schema, policy))
            .collect()
    }

    pub(crate) fn models_for_target(
        &self,
        policy: &Policy,
        target: ClientTarget,
    ) -> anyhow::Result<Vec<Model>> {
        let roots = policy
            .models
            .iter()
            .filter(|item| item.includes(target))
            .map(|item| item.schema.clone())
            .collect::<Vec<_>>();
        let mut ordered = roots.clone();
        let mut known = roots.into_iter().collect::<BTreeSet<_>>();
        let unions = self.unions_for_target(policy, target)?;
        let mut omitted_discriminators = BTreeMap::new();
        for union in unions {
            for variant in union.variants {
                if let Some(existing) = omitted_discriminators
                    .insert(variant.model.clone(), union.discriminator.clone())
                {
                    ensure!(
                        existing == union.discriminator,
                        "union variant {} uses conflicting discriminators {existing} and {}",
                        variant.model,
                        union.discriminator
                    );
                }
                if let Some(explicit) = policy.model_policy(&variant.model) {
                    ensure!(
                        explicit.includes(target),
                        "{} union {} references {} which is excluded from {:?}",
                        target_label(target),
                        union.name,
                        variant.model,
                        target
                    );
                }
                if known.insert(variant.model.clone()) {
                    ordered.push(variant.model);
                }
            }
        }
        let mut queue = VecDeque::from(ordered.clone());
        while let Some(name) = queue.pop_front() {
            let model = self.model_omitting(
                &name,
                policy,
                omitted_discriminators.get(&name).map(String::as_str),
            )?;
            for referenced in model_references(&model) {
                if let Some(explicit) = policy.model_policy(&referenced) {
                    ensure!(
                        explicit.includes(target),
                        "{} model {} references {} which is excluded from {:?}",
                        target_label(target),
                        name,
                        referenced,
                        target
                    );
                }
                if known.insert(referenced.clone()) {
                    ordered.push(referenced.clone());
                    queue.push_back(referenced);
                }
            }
        }
        ordered
            .iter()
            .map(|name| {
                self.model_omitting(
                    name,
                    policy,
                    omitted_discriminators.get(name).map(String::as_str),
                )
            })
            .collect()
    }

    fn validate_policy(&self, policy: &Policy) -> anyhow::Result<()> {
        for item in &policy.enums {
            self.enum_values(&item.schema)?;
        }
        for item in &policy.models {
            self.model(&item.schema, policy)?;
        }
        for item in &policy.unions {
            self.union(&item.schema, policy)?;
        }
        for path in policy
            .settings
            .untyped_fields
            .iter()
            .chain(&policy.settings.lenient_fields)
            .map(String::as_str)
            .chain(policy.field_default_paths())
        {
            let (model, field) = path
                .split_once('.')
                .context("validated field policy lost its separator")?;
            let parsed = self.model(model, policy)?;
            ensure!(
                parsed.fields.iter().any(|item| item.wire_name == field),
                "client field policy points to missing OpenAPI field {path}"
            );
        }
        for target in [ClientTarget::AppSwift, ClientTarget::ShareSwift] {
            self.unions_for_target(policy, target)?;
            self.models_for_target(policy, target)?;
        }
        Ok(())
    }

    fn schema(&self, name: &str) -> anyhow::Result<&Value> {
        self.schemas
            .get(name)
            .with_context(|| format!("OpenAPI component schema {name} is missing"))
    }

    fn parse_type(&self, schema: &Value, path: &str, policy: &Policy) -> anyhow::Result<TypeRef> {
        let object = schema
            .as_object()
            .with_context(|| format!("schema for {path} is not an object"))?;
        let nullable = object.get("nullable").and_then(Value::as_bool) == Some(true)
            || type_array_contains_null(object);

        if let Some(reference) = object.get("$ref").and_then(Value::as_str) {
            return self.reference_type(reference, path, policy, nullable);
        }
        if let Some((concrete, union_nullable)) =
            nullable_union_member(object).with_context(|| format!("{path}: unsupported union"))?
        {
            return self
                .parse_type(concrete, path, policy)
                .map(|item| item.with_nullable(nullable || union_nullable));
        }
        if let Some(items) = object.get("allOf").and_then(Value::as_array) {
            ensure!(
                items.len() == 1,
                "{path}: only single-entry allOf is supported"
            );
            return self
                .parse_type(&items[0], path, policy)
                .map(|item| item.with_nullable(nullable));
        }

        let concrete_type = concrete_type_name(object);
        match concrete_type {
            Some("string") if object.get("format").and_then(Value::as_str) == Some("date-time") => {
                Ok(TypeRef {
                    kind: TypeKind::DateTime,
                    nullable,
                })
            }
            Some("string") => Ok(TypeRef {
                kind: TypeKind::String,
                nullable,
            }),
            Some("integer") => Ok(TypeRef {
                kind: TypeKind::Integer,
                nullable,
            }),
            Some("number") => Ok(TypeRef {
                kind: TypeKind::Float,
                nullable,
            }),
            Some("boolean") => Ok(TypeRef {
                kind: TypeKind::Boolean,
                nullable,
            }),
            Some("array") => {
                let items = object
                    .get("items")
                    .with_context(|| format!("{path}: array schema has no items"))?;
                Ok(TypeRef {
                    kind: TypeKind::List(Box::new(self.parse_type(items, path, policy)?)),
                    nullable,
                })
            }
            Some("object") => self.map_type(object, path, policy, nullable),
            Some("null") => bail!("{path}: bare null is only valid inside an optional union"),
            Some(other) => bail!("{path}: unsupported OpenAPI type {other}"),
            None if object.is_empty() => Ok(TypeRef {
                kind: TypeKind::Untyped,
                nullable,
            }),
            None => bail!("{path}: schema has no supported type, ref, or optional union"),
        }
    }

    fn reference_type(
        &self,
        reference: &str,
        path: &str,
        policy: &Policy,
        nullable: bool,
    ) -> anyhow::Result<TypeRef> {
        let name = reference
            .strip_prefix("#/components/schemas/")
            .with_context(|| format!("{path}: external OpenAPI refs are unsupported"))?;
        self.schema(name)?;
        let kind = if let Some(item) = policy.enum_policy(name) {
            ensure!(
                item.targets.iter().any(|_| true),
                "{path}: enum {name} has no client targets"
            );
            TypeKind::Enum(name.to_owned())
        } else if let Some(item) = policy.union_policy(name) {
            ensure!(
                item.targets.iter().any(|_| true),
                "{path}: union {name} has no client targets"
            );
            TypeKind::Union(name.to_owned())
        } else if self.schema(name)?.get("enum").is_some() {
            bail!("{path}: enum {name} is not in the reviewed client registry");
        } else {
            TypeKind::Model(name.to_owned())
        };
        Ok(TypeRef { kind, nullable })
    }

    fn variant_tag(&self, model: &str, discriminator: &str) -> anyhow::Result<String> {
        let property = self
            .schema(model)?
            .pointer(&format!("/properties/{discriminator}"))
            .with_context(|| {
                format!(
                    "union variant {model} has no discriminator field {discriminator} and no mapping"
                )
            })?;
        self.discriminator_literal(property).with_context(|| {
            format!(
                "union variant {model}.{discriminator} must have a string const or one-value enum"
            )
        })
    }

    fn validate_discriminator_collision(
        &self,
        model: &str,
        discriminator: &str,
        expected_tag: &str,
    ) -> anyhow::Result<()> {
        let Some(property) = self
            .schema(model)?
            .pointer(&format!("/properties/{discriminator}"))
        else {
            return Ok(());
        };
        let literal = self.discriminator_literal(property).with_context(|| {
            format!("union variant {model} field {discriminator} collides with its discriminator")
        })?;
        ensure!(
            literal == expected_tag,
            "union variant {model}.{discriminator} is {literal}, expected {expected_tag}"
        );
        Ok(())
    }

    fn discriminator_literal(&self, schema: &Value) -> Option<String> {
        if let Some(reference) = schema.get("$ref").and_then(Value::as_str) {
            let name = reference.strip_prefix("#/components/schemas/")?;
            return discriminator_literal(self.schemas.get(name)?);
        }
        discriminator_literal(schema)
    }

    fn map_type(
        &self,
        object: &Map<String, Value>,
        path: &str,
        policy: &Policy,
        nullable: bool,
    ) -> anyhow::Result<TypeRef> {
        let additional = object.get("additionalProperties");
        let Some(value_schema) = additional else {
            bail!("{path}: inline object schemas are unsupported; use a named component");
        };
        ensure!(
            value_schema.as_bool() != Some(false),
            "{path}: closed inline object schemas are unsupported"
        );
        let value_type = if value_schema.as_bool() == Some(true) {
            TypeRef {
                kind: TypeKind::Untyped,
                nullable: false,
            }
        } else {
            self.parse_type(value_schema, path, policy)?
        };
        if matches!(value_type.kind, TypeKind::Untyped) {
            ensure!(
                policy.settings.untyped_fields.contains(path),
                "{path}: untyped JSON requires an explicit allowlist entry"
            );
        }
        Ok(TypeRef {
            kind: TypeKind::Map(Box::new(value_type)),
            nullable,
        })
    }
}

fn model_references(model: &Model) -> Vec<String> {
    let mut references = Vec::new();
    for field in &model.fields {
        collect_model_references(&field.type_ref, &mut references);
    }
    references
}

fn collect_model_references(type_ref: &TypeRef, references: &mut Vec<String>) {
    match &type_ref.kind {
        TypeKind::Model(name) => references.push(name.clone()),
        TypeKind::List(item) | TypeKind::Map(item) => collect_model_references(item, references),
        TypeKind::String
        | TypeKind::Integer
        | TypeKind::Float
        | TypeKind::Boolean
        | TypeKind::DateTime
        | TypeKind::Enum(_)
        | TypeKind::Union(_)
        | TypeKind::Untyped => {}
    }
}

fn union_member_reference(member: &Value) -> anyhow::Result<&str> {
    if let Some(reference) = member.get("$ref").and_then(Value::as_str) {
        return Ok(reference);
    }
    let all_of = member
        .get("allOf")
        .and_then(Value::as_array)
        .context("union member is not a ref")?;
    ensure!(
        all_of.len() == 1,
        "union allOf wrapper must have exactly one entry"
    );
    all_of[0]
        .get("$ref")
        .and_then(Value::as_str)
        .context("union allOf entry is not a ref")
}

fn local_reference_name<'a>(reference: &'a str, context: &str) -> anyhow::Result<&'a str> {
    reference
        .strip_prefix("#/components/schemas/")
        .with_context(|| format!("{context}: external OpenAPI refs are unsupported"))
}

fn discriminator_literal(schema: &Value) -> Option<String> {
    if let Some(value) = schema.get("const").and_then(Value::as_str) {
        return Some(value.to_owned());
    }
    let values = schema.get("enum").and_then(Value::as_array)?;
    if values.len() != 1 {
        return None;
    }
    values[0].as_str().map(ToOwned::to_owned)
}

fn ensure_language_variant_names(name: &str, variants: &[UnionVariant]) -> anyhow::Result<()> {
    let mut swift_names = BTreeSet::new();
    for variant in variants {
        let swift = swift_identifier(&variant.tag);
        ensure!(
            swift_names.insert(swift.clone()),
            "union {name} has Swift case-name collision {swift}"
        );
    }
    Ok(())
}

fn swift_identifier(value: &str) -> String {
    let words = value
        .replace('-', "_")
        .split('_')
        .filter(|part| !part.is_empty())
        .map(str::to_ascii_lowercase)
        .collect::<Vec<_>>();
    let mut words = words.into_iter();
    let Some(first) = words.next() else {
        return String::new();
    };
    words.fold(first, |mut output, word| {
        let mut chars = word.chars();
        if let Some(first) = chars.next() {
            output.extend(first.to_uppercase());
            output.extend(chars);
        }
        output
    })
}

fn concrete_type_name(object: &Map<String, Value>) -> Option<&str> {
    match object.get("type") {
        Some(Value::String(value)) => Some(value),
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .find(|value| *value != "null"),
        _ => None,
    }
}

fn type_array_contains_null(object: &Map<String, Value>) -> bool {
    object
        .get("type")
        .and_then(Value::as_array)
        .is_some_and(|items| items.iter().any(|item| item.as_str() == Some("null")))
}

fn nullable_union_member(object: &Map<String, Value>) -> anyhow::Result<Option<(&Value, bool)>> {
    for key in ["oneOf", "anyOf"] {
        let Some(items) = object.get(key).and_then(Value::as_array) else {
            continue;
        };
        if items.len() == 1 {
            return Ok(items.first().map(|item| (item, false)));
        }
        let concrete = items
            .iter()
            .filter(|item| item.get("type").and_then(Value::as_str) != Some("null"))
            .collect::<Vec<_>>();
        let null_count = items.len().saturating_sub(concrete.len());
        if null_count == 1 && concrete.len() == 1 {
            return Ok(concrete.first().copied().map(|item| (item, true)));
        }
        bail!("general oneOf/anyOf schemas are unsupported by client generation");
    }
    Ok(None)
}

const fn target_label(target: ClientTarget) -> &'static str {
    match target {
        ClientTarget::AppSwift => "app Swift",
        ClientTarget::ShareSwift => "Share Extension Swift",
    }
}
