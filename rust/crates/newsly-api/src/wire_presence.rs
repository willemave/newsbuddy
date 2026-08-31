//! `OpenAPI` wire-presence normalization for Rust response types.
//!
//! Serde always serializes every field in the public response structs, including `None` as
//! explicit `null` and default-valued collections/booleans. Utoipa intentionally interprets
//! `Option<T>` and `#[serde(default)]` as input optionality, so its derived `required` arrays do
//! not by themselves describe what the server guarantees on output. This pass makes the response
//! side explicit without making request defaults part of Rust object construction.

use std::collections::{BTreeSet, VecDeque};

use serde_json::Value;
use utoipa::openapi::{
    OpenApi, RefOr,
    schema::{AdditionalProperties, ArrayItems, Schema},
};

const HTTP_METHODS: &[&str] = &[
    "delete", "get", "head", "options", "patch", "post", "put", "trace",
];
const COMPONENT_PREFIX: &str = "#/components/schemas/";

pub(super) fn normalize_response_presence(mut document: OpenApi) -> OpenApi {
    let value = serde_json::to_value(&document).expect("OpenAPI document serializes to JSON");
    let schemas = value
        .pointer("/components/schemas")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    let mut reachable = BTreeSet::new();
    if let Some(paths) = value.get("paths").and_then(Value::as_object) {
        for path_item in paths.values().filter_map(Value::as_object) {
            for operation in HTTP_METHODS
                .iter()
                .filter_map(|method| path_item.get(*method))
            {
                if let Some(responses) = operation.get("responses") {
                    collect_component_names(responses, &mut reachable);
                }
            }
        }
    }

    let mut pending = reachable.iter().cloned().collect::<VecDeque<_>>();
    while let Some(name) = pending.pop_front() {
        let Some(schema) = schemas.get(&name) else {
            continue;
        };
        let mut nested = BTreeSet::new();
        collect_component_names(schema, &mut nested);
        for nested_name in nested {
            if reachable.insert(nested_name.clone()) {
                pending.push_back(nested_name);
            }
        }
    }

    if let Some(components) = document.components.as_mut() {
        for name in reachable {
            if let Some(schema) = components.schemas.get_mut(&name) {
                require_ref_or_properties(schema);
            }
        }
    }

    document
}

fn collect_component_names(value: &Value, names: &mut BTreeSet<String>) {
    match value {
        Value::Array(items) => {
            for item in items {
                collect_component_names(item, names);
            }
        }
        Value::Object(object) => {
            if let Some(name) = object
                .get("$ref")
                .and_then(Value::as_str)
                .and_then(|reference| reference.strip_prefix(COMPONENT_PREFIX))
            {
                names.insert(name.to_owned());
            }
            for item in object.values() {
                collect_component_names(item, names);
            }
        }
        _ => {}
    }
}

fn require_ref_or_properties(schema: &mut RefOr<Schema>) {
    if let RefOr::T(schema) = schema {
        require_schema_properties(schema);
    }
}

fn require_schema_properties(schema: &mut Schema) {
    match schema {
        Schema::Object(object) => {
            object.required = object.properties.keys().cloned().collect();
            for property in object.properties.values_mut() {
                require_ref_or_properties(property);
            }
            if let Some(additional_properties) = object.additional_properties.as_deref_mut()
                && let AdditionalProperties::RefOr(schema) = additional_properties
            {
                require_ref_or_properties(schema);
            }
            if let Some(property_names) = object.property_names.as_deref_mut() {
                require_schema_properties(property_names);
            }
        }
        Schema::Array(array) => {
            if let ArrayItems::RefOrSchema(items) = &mut array.items {
                require_ref_or_properties(items);
            }
            for item in &mut array.prefix_items {
                require_schema_properties(item);
            }
        }
        Schema::OneOf(one_of) => {
            for item in &mut one_of.items {
                require_ref_or_properties(item);
            }
        }
        Schema::AllOf(all_of) => {
            for item in &mut all_of.items {
                require_ref_or_properties(item);
            }
        }
        Schema::AnyOf(any_of) => {
            for item in &mut any_of.items {
                require_ref_or_properties(item);
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn response_reachable_fields_are_required_but_request_defaults_stay_optional() {
        let document: OpenApi = serde_json::from_value(json!({
            "openapi": "3.1.0",
            "info": {"title": "test", "version": "1"},
            "paths": {
                "/items": {
                    "post": {
                        "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Input"}}}},
                        "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Output"}}}}}
                    }
                }
            },
            "components": {"schemas": {
                "Input": {"type": "object", "properties": {"defaulted": {"type": "boolean"}}},
                "Nested": {"type": "object", "properties": {"nullable": {"type": ["string", "null"]}}},
                "Output": {"type": "object", "properties": {"nested": {"$ref": "#/components/schemas/Nested"}}}
            }}
        }))
        .unwrap();

        let value = serde_json::to_value(normalize_response_presence(document)).unwrap();
        assert!(
            value["components"]["schemas"]["Input"]
                .get("required")
                .is_none()
        );
        assert_eq!(
            value["components"]["schemas"]["Output"]["required"],
            json!(["nested"])
        );
        assert_eq!(
            value["components"]["schemas"]["Nested"]["required"],
            json!(["nullable"])
        );
    }
}
